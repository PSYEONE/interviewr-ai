"""Speech and chat models share one card, so the speech model is chosen and loaded against free VRAM."""

from __future__ import annotations

from app.config import Settings
from app.stt.whisper import fits_in_vram
from app.system.hardware import GpuInfo, _nvidia_free_parse
from app.system.whisper_models import (
    llm_vram_mb,
    speech_and_rag_vram_mb,
    vram_needed_mb,
    whisper_choice,
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _gpu(vram_mb: int) -> GpuInfo:
    return GpuInfo(index=1, name="card", vendor="nvidia", kind="discrete", vram_mb=vram_mb)


def test_nvidia_free_memory_is_read_per_card() -> None:
    # One line per GPU; the card with the most free is the one worth loading onto.
    assert _nvidia_free_parse("1364\n7820\n", 0) == 7820
    assert _nvidia_free_parse("", 0) is None
    assert _nvidia_free_parse("1364", 9) is None


def test_a_catalog_model_reserves_its_estimated_vram() -> None:
    reserved = llm_vram_mb(_settings(llm_provider="ollama", llm_model="qwen3:8b"))
    assert reserved > 1000  # weights plus KV cache, from the LLM catalog


def test_an_unknown_local_model_still_reserves_room() -> None:
    assert llm_vram_mb(_settings(llm_provider="ollama", llm_model="something:custom")) == 4096


def test_a_cloud_model_reserves_nothing() -> None:
    cloud = _settings(
        llm_provider="anthropic", llm_model="claude-sonnet-5", anthropic_api_key="test-key"
    )
    assert llm_vram_mb(cloud) == 0


def test_speech_and_retrieval_reserve_their_share() -> None:
    # The mirror of llm_vram_mb: the chat model cannot have what these two already hold.
    default = _settings(llm_provider="ollama", llm_model="qwen3:8b")
    assert speech_and_rag_vram_mb(default) == 1150  # small.en 850 + nomic-embed-text 300


def test_cloud_speech_and_cloud_embeddings_reserve_nothing() -> None:
    cloud = _settings(
        llm_provider="ollama",
        llm_model="qwen3:8b",
        whisper_use_gpu=False,
        embed_provider="openai",
        openai_api_key="test-key",
    )
    assert speech_and_rag_vram_mb(cloud) == 0


def test_retrieval_turned_off_reserves_nothing_for_it() -> None:
    off = _settings(llm_provider="ollama", llm_model="qwen3:8b", rag_enabled=False)
    assert speech_and_rag_vram_mb(off) == 850  # the speech model only


def test_an_8gb_card_running_an_llm_is_not_offered_the_biggest_model() -> None:
    # The crash case: 8 GB card, an 8B-class LLM resident, medium.en then cannot load.
    choice = whisper_choice([_gpu(8192)], True, 5500)
    assert choice.suggested == "small.en"
    # 2.7 GB left: medium.en could just about load, which is exactly how it crashed, so never suggest it.
    assert next(m for m in choice.models if m.name == "medium.en").fit == "tight"


def test_a_big_card_still_gets_the_most_accurate_model() -> None:
    assert whisper_choice([_gpu(24576)], True, 5500).suggested == "medium.en"


def test_a_card_the_llm_fills_counts_as_cpu_only() -> None:
    choice = whisper_choice([_gpu(8192)], True, 8192)
    assert choice.suggested == "base.en"
    assert all(m.fit == "cpu" for m in choice.models)


def test_a_model_needs_headroom_beyond_its_weights() -> None:
    needed = vram_needed_mb("medium.en")
    assert needed is not None
    assert not fits_in_vram(needed, 1364)  # what was free when it segfaulted
    assert not fits_in_vram(needed, needed + 100)  # barely enough is how it crashed
    assert fits_in_vram(needed, needed * 2)


def test_unmeasurable_free_memory_does_not_block_the_gpu() -> None:
    # Intel and other drivers report nothing; falling back to the CPU there would be wrong.
    assert fits_in_vram(2100, None)
