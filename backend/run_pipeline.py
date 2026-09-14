"""CLI entry point: serve the WebSocket backend (or list capture devices)."""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from app.config import get_settings
from app.system.parent import watch


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

    # Exit if the overlay that launched us goes away (app/system/parent.py); startup work runs in the FastAPI lifespan.
    watch()
    # Show the engine's own info logs (capture start and stop); other libraries stay at warnings.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(name)s: %(message)s")
    logging.getLogger("app").setLevel(logging.INFO)
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
