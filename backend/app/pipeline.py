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
        gpu_device=settings.whisper_gpu_device,
    )
    # Pay the one-time Vulkan shader compile now so the first real utterance is fast.
    await asyncio.get_event_loop().run_in_executor(None, transcriber.warmup)
    bus.publish(StatusEvent(state="listening"))
    try:
        audio_stream = capture_audio(device)
        transcript_stream = transcribe_stream(
            audio_stream,
            transcriber,
            silence_rms=settings.vad_silence_rms,
            silence_hold_s=settings.vad_silence_hold_s,
            max_utterance_s=settings.vad_max_utterance_s,
            partial_interval_s=settings.stt_partial_interval_s,
            emit_partials=settings.stt_emit_partials,
        )
        async for item in transcript_stream:
            if item.final:
                if settings.debug_print:
                    print(f"[FINAL] {item.text}")
                session.add_transcript(item.text)
            else:
                session.publish_partial(item.text)
    except asyncio.CancelledError:
        raise
    finally:
        bus.publish(StatusEvent(state="idle"))
