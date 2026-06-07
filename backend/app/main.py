"""FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from .config import get_settings

settings = get_settings()

app = FastAPI(title="interviewr-ai", version="0.0.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "remote_viewer": str(settings.enable_remote_viewer),
    }
