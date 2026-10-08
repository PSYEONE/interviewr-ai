"""Hardware probe for setup: GPUs via Vulkan, RAM and CPU, and a local-LLM recommendation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
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

class CuratedModel(BaseModel):
    """A hand-picked model offered in the model picker."""

    name: str  # ollama tag, e.g. "qwen2.5:14b"
    label: str  # friendly name shown in the picker


class CatalogModel(CuratedModel):
    """A curated model plus its facts from catalog.json (None until the next refresh)."""

    download_mb: int | None = None  # exact, summed from the registry manifest
    params: str | None = None  # "14.8B", from the manifest's config blob
    quant: str | None = None  # "Q4_K_M", likewise
    family: str | None = None  # qwen | llama | phi | gemma | mistral ...


class RankedModel(CatalogModel):
    fit: str | None  # fits | tight | too_big | cpu; None when the size isn't known yet
    vram_mb: int | None  # estimated VRAM to run it, see _KV_OVERHEAD_MB


# Curated Ollama chat models; their facts are generated into catalog.json by scripts/refresh_catalog.py.
_CURATED: list[CuratedModel] = [
    CuratedModel(name="llama3.2:3b", label="Llama 3.2 3B"),
    CuratedModel(name="phi4-mini", label="Phi-4 mini"),
    CuratedModel(name="gemma3:4b", label="Gemma 3 4B"),
    CuratedModel(name="qwen2.5:7b", label="Qwen2.5 7B"),
    CuratedModel(name="qwen3:8b", label="Qwen3 8B"),
    CuratedModel(name="llama3.1:8b", label="Llama 3.1 8B"),
    CuratedModel(name="mistral-nemo:12b", label="Mistral Nemo 12B"),
    CuratedModel(name="phi4", label="Phi-4 14B"),
    CuratedModel(name="qwen2.5:14b", label="Qwen2.5 14B"),
    CuratedModel(name="gemma3:27b", label="Gemma 3 27B"),
    CuratedModel(name="qwen2.5:32b", label="Qwen2.5 32B"),
    CuratedModel(name="llama3.3:70b", label="Llama 3.3 70B"),
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


_CATALOG_JSON = Path(__file__).resolve().parent / "catalog.json"

# KV cache estimate for an 8B-class model at 4096 context; _FITS_HEADROOM covers larger ones.
_KV_OVERHEAD_MB = 512
_FITS_HEADROOM = 1.25  # "fits" means comfortable, not merely possible


@lru_cache(maxsize=1)
def _generated() -> dict[str, dict[str, object]]:
    """Facts from catalog.json keyed by ollama tag; empty if the file is missing or unreadable."""
    try:
        raw = json.loads(_CATALOG_JSON.read_text())
    except (OSError, ValueError):
        return {}
    models = raw.get("models") if isinstance(raw, dict) else None
    return models if isinstance(models, dict) else {}


def catalog() -> list[CatalogModel]:
    """The curated list merged with its generated facts."""
    gen = _generated()
    out: list[CatalogModel] = []
    for m in _CURATED:
        facts = gen.get(m.name, {})
        out.append(CatalogModel(**m.model_dump(), **facts))  # type: ignore[arg-type]
    return out


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
    """Parses nvidia-smi output into (name -> VRAM MB, driver error or None)."""
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
    """NVIDIA name -> VRAM MB plus any driver error; ({}, None) without nvidia-smi or a card."""
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


def _nvidia_free_parse(stdout: str, returncode: int) -> int | None:
    """Free VRAM (MB) from nvidia-smi, taking the card with the most free; None when it said nothing."""
    if returncode != 0:
        return None
    values = [int(v) for v in re.findall(r"\d+", stdout)]
    return max(values) if values else None


def _amd_free_vram_mb() -> int | None:
    """Free VRAM (MB) on the AMD card with the most free, from sysfs; None without one."""
    best: int | None = None
    for total_path in sorted(Path("/sys/class/drm").glob("card*/device/mem_info_vram_total")):
        used_path = total_path.parent / "mem_info_vram_used"
        try:
            free = (int(total_path.read_text()) - int(used_path.read_text())) // (1024 * 1024)
        except (OSError, ValueError):
            continue
        if free > 0 and (best is None or free > best):
            best = free
    return best


def free_vram_mb() -> int | None:
    """Free VRAM (MB) on the GPU we would transcribe on, or None when it can't be measured."""
    if shutil.which("nvidia-smi") is not None:
        try:
            proc = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return _nvidia_free_parse(proc.stdout, proc.returncode)
    return _amd_free_vram_mb()


def _amd_card_vram_mb(vram_total: int, vis_vram_total: int, gtt_total: int) -> int:
    """Usable VRAM (MB) for one AMD card, taking the largest pool so APUs aren't reported as 0."""
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
    """Labels each model's fit for ``vram_mb`` (None = CPU only) and sorts the best fits first."""
    ranked: list[RankedModel] = []
    for m in catalog():
        need = None if m.download_mb is None else m.download_mb + _KV_OVERHEAD_MB
        if need is None:
            fit = None
        elif vram_mb is None:
            fit = "cpu"
        elif vram_mb >= need * _FITS_HEADROOM:
            fit = "fits"
        elif vram_mb >= need:
            fit = "tight"
        else:
            fit = "too_big"
        ranked.append(RankedModel(**m.model_dump(), fit=fit, vram_mb=need))
    if vram_mb is None:
        ranked.sort(key=lambda r: (r.vram_mb is None, r.vram_mb or 0))
        return ranked
    order = {"fits": 0, "tight": 1, "too_big": 2}
    ranked.sort(key=lambda r: (order.get(r.fit or "", 3), -(r.download_mb or 0)))
    return ranked


def _suggested_from(ranked: list[RankedModel]) -> list[str]:
    """Default preselection: the largest fitting models, else the smallest as a fallback."""
    fits = [r.name for r in ranked if r.fit == "fits"]
    if fits:
        return fits[:2]
    smallest = sorted(ranked, key=lambda r: (r.download_mb is None, r.download_mb or 0))
    return [r.name for r in smallest[:2]]


def recommend(gpus: list[GpuInfo], ram_mb: int, gpu_error: str | None = None) -> Recommendation:
    """Local-LLM verdict from the best discrete GPU's VRAM, plus the ranked catalog."""
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
