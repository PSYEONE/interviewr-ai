"""Speech model choices for the setup screen: sizes, measured accuracy, and which one suits the hardware."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..stt.whisper import available_languages
from .hardware import GpuInfo, rank_catalog

if TYPE_CHECKING:
    from ..config import Settings

# Download size, VRAM whisper.cpp needs, and accuracy/speed measured by backend/eval on a laptop
# RTX 3070 (LibriSpeech test-clean, 100 clips, through the streaming pipeline). Those two numbers
# are English only, so the multilingual rows carry None rather than a figure that was never measured.
# Sizes are the Hugging Face files in MiB. large-v3-turbo-q5_0 was measured at 850 MB resident on
# an RTX 3070 (Vulkan); the f16 turbo figure is that measurement with the bigger weights, an estimate.
_MODELS: list[tuple[str, int, int, float | None, float | None]] = [
    # name, download MB, VRAM MB, live word error rate, speed
    ("tiny.en", 75, 275, None, None),
    ("base.en", 142, 390, 0.060, 0.22),
    ("small.en", 466, 850, 0.048, 0.37),
    ("medium.en", 1462, 2100, 0.043, 0.66),
    ("small", 465, 850, None, None),
    ("medium", 1463, 2100, None, None),
    ("large-v3-turbo", 1549, 2000, None, None),
    ("large-v3-turbo-q5_0", 547, 1000, None, None),
]

# For a language other than English. small drifts into a neighbouring language on Cyrillic
# (Bulgarian comes out as Russian), so any GPU gets a large-v3-turbo build: the q5_0 one needs
# about what small does. On the CPU the large encoder is too slow for live use, so small it is.
_MULTILINGUAL_CPU = "small"
_MULTILINGUAL_GPU = "large-v3-turbo-q5_0"
_MULTILINGUAL_BIG = "large-v3-turbo"

# Assumed VRAM for a local LLM that isn't in the catalog, so an unknown model still reserves room.
_UNKNOWN_LLM_VRAM_MB = 4096

# nomic-embed-text, measured at 292 MB resident while retrieval is on.
_EMBED_VRAM_MB = 300

# VRAM from which the next model up is worth its download and slower transcription.
_SMALL_FROM_MB = 2000
_MEDIUM_FROM_MB = 6000


class WhisperOption(BaseModel):
    name: str
    english_only: bool  # a .en model: transcribes English only and ignores the language setting
    download_mb: int
    vram_mb: int  # what whisper.cpp needs on the GPU
    live_wer: float | None  # word error rate through the live pipeline; None when not measured
    speed: float | None  # fraction of real time to transcribe; lower is faster
    fit: str | None  # fits | tight | too_big | cpu, as for the LLM catalog


class WhisperChoice(BaseModel):
    suggested: str  # what the wizard preselects for this machine
    models: list[WhisperOption]
    languages: list[str]  # whisper.cpp language codes; empty when the local STT extra is missing


def vram_needed_mb(name: str) -> int | None:
    """VRAM whisper.cpp needs for this model, or None for a name we don't know."""
    for model, _download_mb, needs_mb, _wer, _speed in _MODELS:
        if model == name:
            return needs_mb
    return None


def llm_vram_mb(settings: Settings, model_override: str | None = None) -> int:
    """VRAM the configured LLM holds while the app runs, since speech and chat share the card. 0 for cloud.

    ``model_override`` labels against a model the user is picking but has not saved yet, which is
    the wizard's whole situation.
    """
    name = model_override or settings.llm_model
    # An override names a local model to assume, so it stands even while a cloud provider is saved,
    # which is the case when the wizard is switching from cloud to local.
    if not name or (model_override is None and settings.llm_provider != "ollama"):
        return 0
    for model in rank_catalog(None):
        if model.name == name:
            return model.vram_mb if model.vram_mb is not None else _UNKNOWN_LLM_VRAM_MB
    return _UNKNOWN_LLM_VRAM_MB


def speech_and_rag_vram_mb(settings: Settings, model_override: str | None = None) -> int:
    """VRAM the speech model and the local embedder hold, which the chat model cannot have.

    The mirror of ``llm_vram_mb``: all three live on one card, so each side has to allow for the
    others or the wizard recommends a combination that does not fit. ``model_override`` is the
    speech model being chosen right now, which is not yet in settings.
    """
    reserved = 0
    if settings.whisper_use_gpu:
        reserved += vram_needed_mb(model_override or settings.whisper_model) or 0
    if settings.rag_enabled and settings.embed_provider == "ollama" and settings.embed_model:
        reserved += _EMBED_VRAM_MB
    return reserved


def _fit(vram_mb: int | None, needs_mb: int) -> str:
    if vram_mb is None:
        return "cpu"
    if vram_mb >= needs_mb * 2:
        return "fits"
    return "tight" if vram_mb >= needs_mb else "too_big"


def suggest_whisper(vram_mb: int | None, language: str = "auto") -> str:
    """The default speech model for this machine and language: bigger is more accurate but slower.

    English and auto keep the .en ladder, whose accuracy is measured; an explicit other language
    needs a multilingual model, or the transcript comes out in English whatever was said.
    """
    if language not in ("auto", "en"):
        if vram_mb is None:
            return _MULTILINGUAL_CPU
        return _MULTILINGUAL_BIG if vram_mb >= _MEDIUM_FROM_MB else _MULTILINGUAL_GPU
    # On CPU, small.en is barely more accurate than base.en but three times the download and twice as slow.
    if vram_mb is None:
        return "base.en"
    if vram_mb >= _MEDIUM_FROM_MB:
        return "medium.en"
    return "small.en" if vram_mb >= _SMALL_FROM_MB else "base.en"


def whisper_choice(
    gpus: list[GpuInfo], use_gpu: bool = True, llm_reserved_mb: int = 0, language: str = "auto"
) -> WhisperChoice:
    """Speech models labelled for the GPU memory left once the local LLM has its share."""
    usable = [g.vram_mb for g in gpus if g.kind == "discrete" and g.vram_mb is not None]
    total_mb = max(usable, default=None) if use_gpu else None
    # A card that the LLM nearly fills counts as no GPU, which is what the crash on an 8 GB card showed.
    vram_mb = None if total_mb is None else max(0, total_mb - llm_reserved_mb) or None
    models = [
        WhisperOption(
            name=name,
            english_only=name.endswith(".en"),
            download_mb=download_mb,
            vram_mb=needs_mb,
            live_wer=live_wer,
            speed=speed,
            fit=_fit(vram_mb, needs_mb),
        )
        for name, download_mb, needs_mb, live_wer, speed in _MODELS
    ]
    return WhisperChoice(
        suggested=suggest_whisper(vram_mb, language),
        models=models,
        languages=available_languages(),
    )
