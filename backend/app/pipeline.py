"""Phase 0 pipeline: PipeWire audio -> whisper.cpp STT -> Ollama LLM -> terminal."""

from __future__ import annotations

import sys

from .audio.capture import capture_audio, get_default_monitor
from .config import Settings
from .llm.ollama import OllamaStreamer
from .stt.whisper import WhisperTranscriber, transcribe_stream

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

MIN_WORDS = 5  # skip LLM call for near-empty transcript chunks


async def run_pipeline(settings: Settings) -> None:
    device = settings.audio_device or await get_default_monitor()
    print(f"{YELLOW}[AUDIO]{RESET} capturing from {device}")

    transcriber = WhisperTranscriber(
        model_name=settings.whisper_model,
        use_gpu=settings.whisper_use_gpu,
    )
    print(f"{YELLOW}[STT]{RESET} whisper.cpp model={settings.whisper_model}")

    llm = OllamaStreamer(
        base_url=settings.ollama_base_url,
        model=settings.llm_model,
    )
    print(f"{YELLOW}[LLM]{RESET} ollama model={settings.llm_model}")

    try:
        audio_stream = capture_audio(device)
        transcript_stream = transcribe_stream(audio_stream, transcriber)

        async for text in transcript_stream:
            print(f"{CYAN}[TRANSCRIPT]{RESET} {text}")

            if len(text.split()) < MIN_WORDS:
                continue

            sys.stdout.write(f"{GREEN}[RESPONSE]{RESET} ")
            sys.stdout.flush()
            async for token in llm.stream_response(text):
                sys.stdout.write(token)
                sys.stdout.flush()
            print()
    finally:
        await llm.close()
