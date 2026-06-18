"""GPU/RAM parsing and the local-LLM capability verdict, on fixtures (no real hardware)."""

from __future__ import annotations

from app.system.hardware import (
    GpuInfo,
    _gpus_from,
    _meminfo_mb,
    _parse_vulkan_summary,
    recommend,
)

# Real `vulkaninfo --summary` device section from an Intel iGPU + RTX 3070 laptop.
_SUMMARY = """\
Devices:
========
GPU0:
\tvendorID           = 0x8086
\tdeviceID           = 0x9a60
\tdeviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
\tdeviceName         = Intel(R) UHD Graphics (TGL GT1)
GPU1:
\tvendorID           = 0x10de
\tdeviceID           = 0x249d
\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
\tdeviceName         = NVIDIA GeForce RTX 3070 Laptop GPU
GPU2:
\tvendorID           = 0x10005
\tdeviceID           = 0x0000
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 21.1.8, 256 bits)
"""


def test_parse_vulkan_summary_blocks() -> None:
    blocks = _parse_vulkan_summary(_SUMMARY)
    assert [b["index"] for b in blocks] == ["0", "1", "2"]
    assert blocks[1]["deviceName"] == "NVIDIA GeForce RTX 3070 Laptop GPU"
    assert blocks[1]["deviceType"] == "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU"


def test_gpus_from_assigns_vendor_kind_and_vram() -> None:
    gpus = _gpus_from(
        _SUMMARY,
        nvidia_vram={"NVIDIA GeForce RTX 3070 Laptop GPU": 8192},
        amd_vram=[],
    )
    assert len(gpus) == 3
    igpu, dgpu, cpu = gpus
    assert (igpu.vendor, igpu.kind, igpu.vram_mb) == ("intel", "integrated", None)
    assert (dgpu.vendor, dgpu.kind, dgpu.vram_mb, dgpu.index) == ("nvidia", "discrete", 8192, 1)
    assert (cpu.vendor, cpu.kind) == ("other", "cpu")


def test_gpus_from_empty_summary() -> None:
    assert _gpus_from(None, {}, []) == []


def test_meminfo_mb() -> None:
    assert _meminfo_mb("MemTotal:       16111380 kB\nMemFree: 100 kB\n") == 15733
    assert _meminfo_mb("Bogus: 1\n") == 0


def _gpu(kind: str, vram: int | None, index: int = 0, vendor: str = "nvidia") -> GpuInfo:
    return GpuInfo(index=index, name="x", vendor=vendor, kind=kind, vram_mb=vram)


def test_recommend_medium_for_8gb_discrete() -> None:
    rec = recommend([_gpu("integrated", None), _gpu("discrete", 8192, index=1)], ram_mb=16000)
    assert rec.can_run_local is True
    assert rec.tier == "medium"
    assert rec.vram_mb == 8192
    assert rec.discrete_gpu_index == 1  # pin the dGPU for whisper


def test_recommend_large_for_16gb() -> None:
    rec = recommend([_gpu("discrete", 20480, index=0)], ram_mb=32000)
    assert rec.tier == "large"
    assert rec.can_run_local is True


def test_recommend_rejects_tiny_vram() -> None:
    rec = recommend([_gpu("discrete", 4096, index=0)], ram_mb=16000)
    assert rec.can_run_local is False
    assert rec.tier == "none"


def test_recommend_no_discrete_gpu_goes_cloud() -> None:
    rec = recommend([_gpu("integrated", None)], ram_mb=16000)
    assert rec.can_run_local is False
    assert rec.discrete_gpu_index is None
    assert rec.suggested_models  # still suggests tiny models as a fallback
