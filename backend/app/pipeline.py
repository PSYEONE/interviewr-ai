"""Transcript producer: PipeWire audio -> whisper.cpp STT -> Session/EventBus."""

from __future__ import annotations

import asyncio

from .audio.capture import capture_audio, get_default_monitor
from .config import Settings
from .events import EventBus, StatusEvent
from .session import Session
from .stt.whisper import WhisperTranscriber, transcribe_stream


async def run_producer(settings: Settings, session: Session, bus: EventBus) -> None:
    """Continuously transcribe captured audio and feed it into the session.

    No LLM call here: answers are generated on demand via a client command. This
    runs as a background task for the lifetime of the server.
    """
    device = settings.audio_device or await get_default_monitor()
    transcriber = WhisperTranscriber(
        model_name=settings.whisper_model,
        use_gpu=settings.whisper_use_gpu,
    )
    bus.publish(StatusEvent(state="listening"))
    try:
        audio_stream = capture_audio(device)
        async for text in transcribe_stream(audio_stream, transcriber):
            if settings.debug_print:
                print(f"[TRANSCRIPT] {text}")
            session.add_transcript(text)
    except asyncio.CancelledError:
        raise
    finally:
        bus.publish(StatusEvent(state="idle"))
