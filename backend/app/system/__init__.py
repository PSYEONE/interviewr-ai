"""Report for the setup screens: GPUs and RAM, a local-LLM recommendation, and Ollama status, combined into one ``SystemReport``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .disk import DiskInfo, probe_disk
from .hardware import GpuInfo, Recommendation, detect_system, recommend
from .ollama_admin import INSTALL_COMMAND, ModelInfo, OllamaAdmin
from .whisper_models import (
    WhisperChoice,
    WhisperOption,
    llm_vram_mb,
    speech_and_rag_vram_mb,
    whisper_choice,
)

if TYPE_CHECKING:
    from ..config import Settings


class OllamaStatus(BaseModel):
    installed: bool
    running: bool
    version: str | None
    models: list[ModelInfo]
    install_command: str | None  # set only when Ollama is not installed


class SystemReport(BaseModel):
    gpus: list[GpuInfo]
    cpu_count: int
    ram_mb: int
    gpu_error: str | None  # GPU present but driver unreachable; lets the UI say so
    disk: DiskInfo | None  # None when Ollama's model store can't be located (e.g. remote)
    ollama: OllamaStatus
    recommendation: Recommendation
    whisper: WhisperChoice  # speech models, labelled for this machine


async def collect_system_report(
    settings: Settings,
    *,
    whisper_model: str | None = None,
    llm_model: str | None = None,
    assume_recommended_llm: bool = False,
) -> SystemReport:
    """Gather hardware + Ollama status. Hardware probes are blocking subprocesses, so they
    run in a thread; Ollama calls are async httpx.

    The two model lists are labelled against each other's VRAM, so a caller mid-choice passes what
    it is choosing. ``assume_recommended_llm`` is for the wizard's first screen, where no chat model
    has been picked yet: speech sizes are judged against the one about to be recommended rather than
    against whatever happens to be configured.
    """
    info = await asyncio.to_thread(detect_system)
    disk = await asyncio.to_thread(probe_disk, settings.resolved_ollama_models_dir())
    admin = OllamaAdmin(settings.ollama_base_url)
    try:
        version = await admin.version()
        models = await admin.list_models() if version else []
    finally:
        await admin.close()
    installed = OllamaAdmin.is_installed()
    ollama = OllamaStatus(
        installed=installed,
        running=version is not None,
        version=version,
        models=models,
        install_command=None if installed else INSTALL_COMMAND,
    )
    # Chat models first, from the speech choice; then speech models from that result. One pass
    # each way, so the mutual dependency never becomes a loop.
    recommendation = recommend(
        info.gpus, info.ram_mb, info.gpu_error, speech_and_rag_vram_mb(settings, whisper_model)
    )
    chat_model = llm_model
    if chat_model is None and assume_recommended_llm and recommendation.suggested_models:
        chat_model = recommendation.suggested_models[0]
    return SystemReport(
        gpus=info.gpus,
        cpu_count=info.cpu_count,
        ram_mb=info.ram_mb,
        gpu_error=info.gpu_error,
        disk=disk,
        ollama=ollama,
        recommendation=recommendation,
        whisper=whisper_choice(
            info.gpus, settings.whisper_use_gpu, llm_vram_mb(settings, chat_model)
        ),
    )


__all__ = [
    "DiskInfo",
    "GpuInfo",
    "ModelInfo",
    "OllamaStatus",
    "Recommendation",
    "SystemReport",
    "WhisperChoice",
    "WhisperOption",
    "collect_system_report",
]
