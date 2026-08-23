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

class CatalogModel(BaseModel):
    name: str  # ollama tag, e.g. "qwen2.5:14b"
    label: str  # friendly name shown in the picker
    params: str  # "14B"
    min_vram_mb: int  # approx VRAM to run the Q4 build responsively (weights + KV/context headroom)
    family: str  # qwen | llama | phi | gemma | mistral ...
    blurb: str  # one-line use-case note


class RankedModel(CatalogModel):
    fit: str  # fits | tight | too_big | cpu, relative to the detected VRAM


# Curated Ollama chat models spanning 3B..70B (real, currently-published tags). min_vram_mb is
# deliberately generous so "fits" means comfortable with context headroom, not the bare weights.
# Verified against ollama.com/library (June 2026).
_CATALOG: list[CatalogModel] = [
    CatalogModel(
        name="llama3.2:3b", label="Llama 3.2 3B", params="3B", min_vram_mb=3000, family="llama",
        blurb="Tiny and fast. Runs almost anywhere; weakest reasoning.",
    ),
    CatalogModel(
        name="phi4-mini", label="Phi-4 mini", params="3.8B", min_vram_mb=3500, family="phi",
        blurb="Small reasoning model that punches above its size.",
    ),
    CatalogModel(
        name="gemma3:4b", label="Gemma 3 4B", params="4B", min_vram_mb=4000, family="gemma",
        blurb="Compact chat with a long 128K context.",
    ),
    CatalogModel(
        name="qwen2.5:7b", label="Qwen2.5 7B", params="7B", min_vram_mb=6000, family="qwen",
        blurb="Solid all-rounder with strong instruction following.",
    ),
    CatalogModel(
        name="qwen3:8b", label="Qwen3 8B", params="8B", min_vram_mb=6500, family="qwen",
        blurb="Newer Qwen; strong reasoning for the size.",
    ),
    CatalogModel(
        name="llama3.1:8b", label="Llama 3.1 8B", params="8B", min_vram_mb=6500, family="llama",
        blurb="Reliable general chat with good tool use.",
    ),
    CatalogModel(
        name="mistral-nemo:12b", label="Mistral Nemo 12B", params="12B", min_vram_mb=9000,
        family="mistral", blurb="12B with long context; fast for its size.",
    ),
    CatalogModel(
        name="phi4", label="Phi-4 14B", params="14B", min_vram_mb=10000, family="phi",
        blurb="14B reasoning model that rivals much larger ones.",
    ),
    CatalogModel(
        name="qwen2.5:14b", label="Qwen2.5 14B", params="14B", min_vram_mb=10500, family="qwen",
        blurb="Great quality/size balance; a strong default.",
    ),
    CatalogModel(
        name="gemma3:27b", label="Gemma 3 27B", params="27B", min_vram_mb=21000, family="gemma",
        blurb="High-quality 27B; wants a 24GB+ card.",
    ),
    CatalogModel(
        name="qwen2.5:32b", label="Qwen2.5 32B", params="32B", min_vram_mb=24000, family="qwen",
        blurb="Top local quality; needs 24GB+ VRAM.",
    ),
    CatalogModel(
        name="llama3.3:70b", label="Llama 3.3 70B", params="70B", min_vram_mb=44000, family="llama",
        blurb="Flagship quality; needs 48GB+ or multiple GPUs.",
    ),
]


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
    suggested_models: list[str]  # default picks (largest fitting first), drives the preselection
    catalog: list[RankedModel]  # full curated catalog ranked + fit-labelled for this machine
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


def rank_catalog(vram_mb: int | None) -> list[RankedModel]:
    """Fit-label and order the curated catalog for a machine with ``vram_mb`` of VRAM.

    ``None`` means no usable discrete GPU: everything is CPU-only ("cpu"), smallest first since
    those are the only ones tolerable on CPU. With a GPU: "fits" when the card meets the model's
    generous footprint, "tight" within 20% under it, else "too_big". Fitting models come first,
    biggest-that-fits at the top (best quality), then tight, then too_big.
    """
    ranked: list[RankedModel] = []
    for m in _CATALOG:
        if vram_mb is None:
            fit = "cpu"
        elif vram_mb >= m.min_vram_mb:
            fit = "fits"
        elif vram_mb >= int(m.min_vram_mb * 0.8):
            fit = "tight"
        else:
            fit = "too_big"
        ranked.append(RankedModel(**m.model_dump(), fit=fit))
    if vram_mb is None:
        ranked.sort(key=lambda r: r.min_vram_mb)
        return ranked
    order = {"fits": 0, "tight": 1, "too_big": 2}
    ranked.sort(key=lambda r: (order[r.fit], -r.min_vram_mb))
    return ranked


def _suggested_from(ranked: list[RankedModel]) -> list[str]:
    """Default preselection: the largest fitting models, else the smallest as a fallback."""
    fits = [r.name for r in ranked if r.fit == "fits"]
    if fits:
        return fits[:2]
    return [r.name for r in sorted(ranked, key=lambda r: r.min_vram_mb)[:2]]


def recommend(gpus: list[GpuInfo], ram_mb: int, gpu_error: str | None = None) -> Recommendation:
    """Verdict on running a decent local LLM, based on the best discrete GPU's VRAM.

    Integrated GPUs share system RAM and CPU fallback is slow, so "can run local" keys off a
    discrete card with enough VRAM for a responsive 7B+ model. Thresholds are deliberately
    conservative (a 7-8B Q4 model needs ~5-6 GB plus headroom). gpu_error, when set, means a
    GPU exists but its driver is unreachable, so we say that rather than "no GPU". The full curated
    catalog is always returned, fit-labelled against the detected VRAM, so the picker can show
    every model and let the user knowingly pick a bigger (slower) one.
    """
    discrete = [g for g in gpus if g.kind == "discrete" and g.vram_mb is not None]
    best = max(discrete, key=lambda g: g.vram_mb or 0, default=None)
    catalog = rank_catalog(best.vram_mb if best else None)
    suggested = _suggested_from(catalog)
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
            suggested_models=suggested,
            catalog=catalog,
            discrete_gpu_index=None,
            reason=reason,
        )
    vram = best.vram_mb
    if vram >= 15000:
        tier = "large"
    elif vram >= 7500:
        tier = "medium"
    elif vram >= 5500:
        tier = "small"
    else:
        return Recommendation(
            can_run_local=False,
            tier="none",
            vram_mb=vram,
            suggested_models=suggested,
            catalog=catalog,
            discrete_gpu_index=best.index,
            reason=f"{best.name} has only {vram} MB VRAM, too little for a responsive 7B+ "
            "model. Consider cloud, or a tiny local model.",
        )
    return Recommendation(
        can_run_local=True,
        tier=tier,
        vram_mb=vram,
        suggested_models=suggested,
        catalog=catalog,
        discrete_gpu_index=best.index,
        reason=f"{best.name} with {vram} MB VRAM can run {tier} local models.",
    )
