"""System-capability surface for the setup flow: GPU/RAM probe, local-LLM verdict, and
Ollama status. Composed into a single ``SystemReport`` the onboarding wizard's GPU-check
screen consumes. Backend-owned so any client (overlay, phone, browser) can drive it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .hardware import GpuInfo, Recommendation, detect_system, recommend
from .ollama_admin import INSTALL_COMMAND, ModelInfo, OllamaAdmin

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
    ollama: OllamaStatus
    recommendation: Recommendation


async def collect_system_report(settings: Settings) -> SystemReport:
    """Gather hardware + Ollama status. Hardware probes are blocking subprocesses, so they
    run in a thread; Ollama calls are async httpx."""
    info = await asyncio.to_thread(detect_system)
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
    return SystemReport(
        gpus=info.gpus,
        cpu_count=info.cpu_count,
        ram_mb=info.ram_mb,
        ollama=ollama,
        recommendation=recommend(info.gpus, info.ram_mb),
    )


__all__ = [
    "GpuInfo",
    "ModelInfo",
    "OllamaStatus",
    "Recommendation",
    "SystemReport",
    "collect_system_report",
]
