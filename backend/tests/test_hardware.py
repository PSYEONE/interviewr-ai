"""GPU/RAM parsing and the local-LLM capability verdict, on fixtures (no real hardware)."""

from __future__ import annotations

from app.system.hardware import (
    _CURATED,
    GpuInfo,
    _amd_card_vram_mb,
    _gpus_from,
    _meminfo_mb,
    _nvidia_parse,
    _parse_vulkan_summary,
    rank_catalog,
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


def test_recommend_surfaces_driver_error() -> None:
    rec = recommend([], ram_mb=16000, gpu_error="Driver/library version mismatch")
    assert rec.can_run_local is False
    assert "driver" in rec.reason.lower()
    assert "Driver/library version mismatch" in rec.reason


def test_rank_catalog_covers_every_model() -> None:
    ranked = rank_catalog(20480)
    assert {r.name for r in ranked} == {m.name for m in _CURATED}
    assert all(r.fit in {"fits", "tight", "too_big"} for r in ranked)


def test_rank_catalog_labels_fit_against_vram() -> None:
    """Sizes come from catalog.json, so these assert the rule, not hand-tuned constants."""
    by_name = {r.name: r for r in rank_catalog(8192)}
    assert by_name["qwen2.5:7b"].fit == "fits"  # ~5.4GB needed, under 8GB with room to spare
    assert by_name["mistral-nemo:12b"].fit == "too_big"  # ~8.1GB running: more than the card has
    assert by_name["qwen2.5:32b"].fit == "too_big"  # ~23GB, nowhere near


def test_the_runtime_estimate_matches_what_ollama_reports() -> None:
    """The flat 512 MB overhead was 500 MB light on an 8B, which is how an unfittable model got
    recommended. Ollama reports 6.0 GB resident for qwen3:8b's 4983 MB download."""
    by_name = {r.name: r for r in rank_catalog(None)}
    need = by_name["qwen3:8b"].vram_mb
    assert need is not None
    assert 5700 <= need <= 6300


def test_phi4_does_not_fit_an_8gb_card() -> None:
    """Regression: the old hand-tuned 10000 called this 'tight' when it needs ~9.1GB."""
    by_name = {r.name: r for r in rank_catalog(8192)}
    assert by_name["phi4"].vram_mb is not None and by_name["phi4"].vram_mb > 8192
    assert by_name["phi4"].fit == "too_big"


def test_rank_catalog_orders_fitting_biggest_first() -> None:
    ranked = rank_catalog(24576)  # a 24GB card
    fits = [r for r in ranked if r.fit == "fits"]
    assert fits[0].name == "phi4"  # largest that fits leads; a 32B Q4 is only tight on 24GB
    sizes = [r.download_mb or 0 for r in fits]
    assert sizes == sorted(sizes, reverse=True)
    # fitting models all precede non-fitting ones
    first_non_fit = next(i for i, r in enumerate(ranked) if r.fit != "fits")
    assert all(r.fit == "fits" for r in ranked[:first_non_fit])


def test_a_reservation_rules_out_a_model_the_card_cannot_also_hold() -> None:
    """The real fault: an 8GB card was offered qwen3:8b while whisper and the embedder were
    resident too, so Ollama ran 14% of it on the CPU and evicted it between answers."""
    plain = {r.name: r for r in rank_catalog(8192)}
    shared = {r.name: r for r in rank_catalog(8192, 1150)}  # small.en plus nomic-embed-text
    assert plain["qwen3:8b"].fit == "fits"
    assert shared["qwen3:8b"].fit == "tight"
    assert shared["gemma3:4b"].fit == "fits"  # what actually runs wholly on the GPU here


def test_a_reservation_does_not_change_the_tier() -> None:
    # The tier decides whether local is viable at all; a small card can still run a small model.
    gpus = [_gpu("discrete", 8192)]
    assert recommend(gpus, 32000, None, 1150).tier == recommend(gpus, 32000).tier == "medium"


def test_rank_catalog_cpu_when_no_gpu() -> None:
    ranked = rank_catalog(None)
    assert all(r.fit == "cpu" for r in ranked)
    assert (ranked[0].download_mb or 0) <= (ranked[-1].download_mb or 0)  # smallest first


def test_recommend_populates_ranked_catalog() -> None:
    rec = recommend([_gpu("discrete", 20480, index=0)], ram_mb=32000)
    assert {r.name for r in rec.catalog} == {m.name for m in _CURATED}
    assert rec.suggested_models  # largest fitting first
    assert rec.suggested_models[0] in {r.name for r in rec.catalog if r.fit == "fits"}


def test_nvidia_parse_ok() -> None:
    vram, err = _nvidia_parse(
        "NVIDIA GeForce RTX 3070 Laptop GPU, 8192\nNVIDIA GeForce RTX 4090, 24564\n", "", 0
    )
    assert err is None
    assert vram == {"NVIDIA GeForce RTX 3070 Laptop GPU": 8192, "NVIDIA GeForce RTX 4090": 24564}


def test_nvidia_parse_driver_mismatch() -> None:
    vram, err = _nvidia_parse("", "Failed to initialize NVML: Driver/library version mismatch", 255)
    assert vram == {}
    assert err is not None
    assert "mismatch" in err.lower()


def test_nvidia_parse_no_card_is_not_an_error() -> None:
    # nvidia-smi installed but no NVIDIA GPU: nonzero exit, no known marker.
    vram, err = _nvidia_parse("", "No devices were found", 6)
    assert vram == {}
    assert err is None


def test_amd_card_vram_prefers_largest_pool() -> None:
    gb = 1024 * 1024 * 1024
    assert _amd_card_vram_mb(20 * gb, 256 * 1024 * 1024, 16 * gb) == 20480  # discrete: vram_total
    assert _amd_card_vram_mb(256 * 1024 * 1024, 32 * gb, 0) == 32768  # APU: vis_vram_total wins
    assert _amd_card_vram_mb(0, 0, 8 * gb) == 8192  # only GTT exposed
    assert _amd_card_vram_mb(0, 0, 0) == 0
