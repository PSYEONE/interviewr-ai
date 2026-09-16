"""Speech model choices for the setup screen: sizes, measured accuracy, and which one suits the hardware."""

from __future__ import annotations

from pydantic import BaseModel

from .hardware import GpuInfo

# Download size, VRAM whisper.cpp needs, and accuracy/speed measured by backend/eval on a laptop
# RTX 3070 (LibriSpeech test-clean, 100 clips, through the streaming pipeline).
_MODELS: list[tuple[str, int, int, float | None, float | None]] = [
    # name, download MB, VRAM MB, live word error rate, speed
    ("tiny.en", 75, 275, None, None),
    ("base.en", 142, 390, 0.060, 0.22),
    ("small.en", 466, 850, 0.048, 0.37),
    ("medium.en", 1462, 2100, 0.043, 0.66),
]

# VRAM from which the next model up is worth its download and slower transcription.
_SMALL_FROM_MB = 2000
_MEDIUM_FROM_MB = 6000


class WhisperOption(BaseModel):
    name: str
    download_mb: int
    vram_mb: int  # what whisper.cpp needs on the GPU
    live_wer: float | None  # word error rate through the live pipeline; None when not measured
    speed: float | None  # fraction of real time to transcribe; lower is faster
    fit: str | None  # fits | tight | too_big | cpu, as for the LLM catalog


class WhisperChoice(BaseModel):
    suggested: str  # what the wizard preselects for this machine
    models: list[WhisperOption]


def _fit(vram_mb: int | None, needs_mb: int) -> str:
    if vram_mb is None:
        return "cpu"
    if vram_mb >= needs_mb * 2:
        return "fits"
    return "tight" if vram_mb >= needs_mb else "too_big"


def suggest_whisper(vram_mb: int | None) -> str:
    """The default speech model for this machine: bigger is more accurate but slower."""
    # On CPU, small.en is barely more accurate than base.en but three times the download and twice as slow.
    if vram_mb is None:
        return "base.en"
    if vram_mb >= _MEDIUM_FROM_MB:
        return "medium.en"
    return "small.en" if vram_mb >= _SMALL_FROM_MB else "base.en"


def whisper_choice(gpus: list[GpuInfo], use_gpu: bool = True) -> WhisperChoice:
    """Speech models labelled for the best GPU present, and the one to preselect."""
    usable = [g.vram_mb for g in gpus if g.kind == "discrete" and g.vram_mb is not None]
    vram_mb = max(usable, default=None) if use_gpu else None
    models = [
        WhisperOption(
            name=name,
            download_mb=download_mb,
            vram_mb=needs_mb,
            live_wer=live_wer,
            speed=speed,
            fit=_fit(vram_mb, needs_mb),
        )
        for name, download_mb, needs_mb, live_wer, speed in _MODELS
    ]
    return WhisperChoice(suggested=suggest_whisper(vram_mb), models=models)
