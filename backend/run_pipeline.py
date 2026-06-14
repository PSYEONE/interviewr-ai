"""CLI entry point for Phase 0 pipeline."""

from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.llm.ollama import OllamaStreamer
from app.pipeline import run_pipeline


async def _preflight(settings: object) -> None:
    """Validate Ollama is reachable and model is configured."""
    from app.config import Settings

    assert isinstance(settings, Settings)
    if not settings.llm_model:
        print("Set INTERVIEWR_LLM_MODEL in .env (e.g. qwen3:8b)")
        sys.exit(1)

    llm = OllamaStreamer(
        settings.ollama_base_url, settings.llm_model, settings.llm_keep_alive
    )
    try:
        if not await llm.check_health():
            print(f"Cannot reach Ollama at {settings.ollama_base_url}")
            sys.exit(1)
        print(f"Warming up {settings.llm_model} (loading into memory)...")
        await llm.warmup()
    finally:
        await llm.close()


def main() -> None:
    settings = get_settings()
    asyncio.run(_preflight(settings))
    print("Starting pipeline (Ctrl+C to stop)\n")
    try:
        asyncio.run(run_pipeline(settings))
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
