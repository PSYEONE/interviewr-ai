"""Hardware probe for the setup flow: enumerate GPUs as Vulkan sees them (the STT backend's
own view, so device indices line up with INTERVIEWR_WHISPER_GPU_DEVICE), read system RAM/CPU,
and turn that into a local-LLM capability verdict.

Linux-first and best-effort: vulkaninfo / nvidia-smi are optional externals. Missing tools
degrade to partial info, never raise. The pure parsing/recommendation helpers take their
inputs as arguments so they are unit-testable without real hardware.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

# PCI vendor ids seen in vulkaninfo's `vendorID` field.
_VENDORS: dict[int, str] = {0x8086: "intel", 0x10DE: "nvidia", 0x1002: "amd"}

_DEVICE_TYPES: dict[str, str] = {
    "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU": "discrete",
    "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU": "integrated",
    "PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU": "virtual",
    "PHYSICAL_DEVICE_TYPE_CPU": "cpu",
}

# Suggested Ollama tags per capability tier (real, currently-published tags).
_MODELS_LARGE: list[str] = ["qwen2.5:14b", "llama3.1:8b"]
_MODELS_MEDIUM: list[str] = ["llama3.1:8b", "qwen2.5:7b"]
_MODELS_SMALL: list[str] = ["llama3.2:3b", "phi3.5"]


class GpuInfo(BaseModel):
    index: int  # Vulkan device index; pin the discrete one via INTERVIEWR_WHISPER_GPU_DEVICE
    name: str
    vendor: str  # intel | nvidia | amd | other
    kind: str  # discrete | integrated | virtual | cpu | other
    vram_mb: int | None  # dedicated VRAM where determinable (nvidia/amd); None = shared/unknown


class SystemInfo(BaseModel):
    gpus: list[GpuInfo]
    cpu_count: int
    ram_mb: int
    gpu_error: str | None = None  # nvidia-smi present but driver unreachable


class Recommendation(BaseModel):
    can_run_local: bool
    tier: str  # large | medium | small | none
    vram_mb: int | None  # the discrete VRAM the verdict is based on
    suggested_models: list[str]
    discrete_gpu_index: int | None  # Vulkan index to pin for whisper on iGPU+dGPU machines
    reason: str


def _run(cmd: list[str]) -> str | None:
    """Run a probe command, returning stdout or None if the tool is absent or fails."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _parse_vulkan_summary(text: str) -> list[dict[str, str]]:
    """Split `vulkaninfo --summary` into one key->value dict per GPU block."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        header = re.match(r"^GPU(\d+):\s*$", line)
        if header:
            current = {"index": header.group(1)}
            blocks.append(current)
            continue
        if current is not None and "=" in line and line[:1] in (" ", "\t"):
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    return blocks


def _gpus_from(summary: str | None, nvidia_vram: dict[str, int], amd_vram: list[int]) -> list[GpuInfo]:
    """Build GpuInfo records from a vulkaninfo summary plus per-vendor VRAM lookups."""
    if not summary:
        return []
    gpus: list[GpuInfo] = []
    amd_cursor = 0
    for block in _parse_vulkan_summary(summary):
        vendor_id = int(block["vendorID"], 16) if block.get("vendorID") else 0
        vendor = _VENDORS.get(vendor_id, "other")
        name = block.get("deviceName", "unknown")
        vram: int | None = None
        if vendor == "nvidia":
            vram = nvidia_vram.get(name)
        elif vendor == "amd" and amd_cursor < len(amd_vram):
            vram = amd_vram[amd_cursor]
            amd_cursor += 1
        gpus.append(
            GpuInfo(
                index=int(block["index"]),
                name=name,
                vendor=vendor,
                kind=_DEVICE_TYPES.get(block.get("deviceType", ""), "other"),
                vram_mb=vram,
            )
        )
    return gpus


def _nvidia_parse(stdout: str, stderr: str, returncode: int) -> tuple[dict[str, int], str | None]:
    """Turn one nvidia-smi run into (name -> VRAM MB, driver-error string).

    nvidia-smi can exit nonzero while printing a driver fault (e.g. the kernel
    module was updated without a reboot: "Driver/library version mismatch"). When
    that happens Vulkan can't see the card either, so it would silently vanish and
    look like "no GPU"; surface the fault instead. A clean nonzero exit with no
    known marker (e.g. simply no NVIDIA card present) yields ({}, None).
    """
    combined = f"{stdout}\n{stderr}".lower()
    markers = (
        "driver/library version mismatch",
        "couldn't communicate with the nvidia driver",
        "failed to initialize nvml",
    )
    if any(marker in combined for marker in markers):
        lines = (stdout + stderr).strip().splitlines()
        return {}, (lines[0][:140] if lines else "NVIDIA driver error")
    if returncode != 0:
        return {}, None
    result: dict[str, int] = {}
    for line in stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            result[parts[0]] = int(parts[1])
    return result, None


def _nvidia_probe() -> tuple[dict[str, int], str | None]:
    """Map NVIDIA device name -> total VRAM (MB), plus a driver-error string when
    nvidia-smi is present but the driver is unreachable. Names match vulkaninfo's
    deviceName. ({}, None) means no nvidia-smi or no NVIDIA card."""
    if shutil.which("nvidia-smi") is None:
        return {}, None
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}, None
    return _nvidia_parse(proc.stdout, proc.stderr, proc.returncode)


def _amd_card_vram_mb(vram_total: int, vis_vram_total: int, gtt_total: int) -> int:
    """Usable VRAM (MB) for one AMD card from its sysfs mem_info_* byte counts.

    Discrete cards report real VRAM in mem_info_vram_total. APUs / unified-memory
    SoCs (e.g. Strix Halo) report a tiny vram_total with the real usable pool in
    vis_vram_total, or expose only gtt_total; take the largest so an APU isn't
    reported as ~0 MB and dropped. Returns 0 when nothing is determinable.
    """
    best = max(vram_total, vis_vram_total)
    if best <= 0:
        best = gtt_total
    return best // (1024 * 1024) if best > 0 else 0


def _amd_vram() -> list[int]:
    """Total VRAM (MB) per AMD card, in sysfs card order."""

    def _read_int(path: Path) -> int:
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            return 0

    values: list[int] = []
    for vram_path in sorted(Path("/sys/class/drm").glob("card*/device/mem_info_vram_total")):
        base = vram_path.parent
        mb = _amd_card_vram_mb(
            _read_int(vram_path),
            _read_int(base / "mem_info_vis_vram_total"),
            _read_int(base / "mem_info_gtt_total"),
        )
        if mb > 0:
            values.append(mb)
    return values


def _meminfo_mb(text: str) -> int:
    """Parse MemTotal (kB) from /proc/meminfo content into MB."""
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) // 1024
            except (IndexError, ValueError):
                return 0
    return 0


def detect_gpus() -> list[GpuInfo]:
    return _gpus_from(_run(["vulkaninfo", "--summary"]), _nvidia_probe()[0], _amd_vram())


def detect_system() -> SystemInfo:
    try:
        ram_mb = _meminfo_mb(Path("/proc/meminfo").read_text())
    except OSError:
        ram_mb = 0
    nvidia_vram, gpu_error = _nvidia_probe()
    gpus = _gpus_from(_run(["vulkaninfo", "--summary"]), nvidia_vram, _amd_vram())
    return SystemInfo(
        gpus=gpus, cpu_count=os.cpu_count() or 0, ram_mb=ram_mb, gpu_error=gpu_error
    )


def recommend(gpus: list[GpuInfo], ram_mb: int, gpu_error: str | None = None) -> Recommendation:
    """Verdict on running a decent local LLM, based on the best discrete GPU's VRAM.

    Integrated GPUs share system RAM and CPU fallback is slow, so "can run local" keys off a
    discrete card with enough VRAM for a responsive 7B+ model. Thresholds are deliberately
    conservative (a 7-8B Q4 model needs ~5-6 GB plus headroom). gpu_error, when set, means a
    GPU exists but its driver is unreachable, so we say that rather than "no GPU".
    """
    discrete = [g for g in gpus if g.kind == "discrete" and g.vram_mb is not None]
    best = max(discrete, key=lambda g: g.vram_mb or 0, default=None)
    if best is None or best.vram_mb is None:
        reason = (
            f"GPU driver error: {gpu_error}. The card is installed but unusable until the "
            "driver is fixed (often a reboot after a driver update). Local models fall back "
            "to CPU and will be slow until then."
            if gpu_error
            else "No discrete GPU detected. Local models run on CPU and will be slow; "
            "cloud is recommended for responsive answers."
        )
        return Recommendation(
            can_run_local=False,
            tier="none",
            vram_mb=None,
            suggested_models=_MODELS_SMALL,
            discrete_gpu_index=None,
            reason=reason,
        )
    vram = best.vram_mb
    if vram >= 15000:
        tier, models = "large", _MODELS_LARGE
    elif vram >= 7500:
        tier, models = "medium", _MODELS_MEDIUM
    elif vram >= 5500:
        tier, models = "small", _MODELS_SMALL
    else:
        return Recommendation(
            can_run_local=False,
            tier="none",
            vram_mb=vram,
            suggested_models=_MODELS_SMALL,
            discrete_gpu_index=best.index,
            reason=f"{best.name} has only {vram} MB VRAM, too little for a responsive 7B+ "
            "model. Consider cloud, or a tiny local model.",
        )
    return Recommendation(
        can_run_local=True,
        tier=tier,
        vram_mb=vram,
        suggested_models=models,
        discrete_gpu_index=best.index,
        reason=f"{best.name} with {vram} MB VRAM can run {tier} local models.",
    )
