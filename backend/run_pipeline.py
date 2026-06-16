"""CLI entry point: serve the WebSocket backend (or list capture devices)."""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from app.config import get_settings


async def _list_devices() -> None:
    """Print available capture targets and the id to set as INTERVIEWR_AUDIO_DEVICE."""
    from app.audio.capture import list_capture_targets

    for t in await list_capture_targets():
        print(f"  [{t.kind:7}] {t.label:24} -> {t.id}")
    print("\nSet INTERVIEWR_AUDIO_DEVICE to one of the ids above (empty = default monitor).")


def main() -> None:
    if "--list-devices" in sys.argv[1:]:
        asyncio.run(_list_devices())
        return

    # Preflight (ollama health + model warmup) and the capture producer run inside the
    # FastAPI lifespan, on uvicorn's single event loop. One loop for the whole lifetime
    # avoids the "Event loop is closed" teardown race the old double-asyncio.run had.
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
