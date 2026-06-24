"""Transcript producer: PipeWire audio -> whisper.cpp STT -> Session/EventBus."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .audio.capture import capture_audio, get_default_monitor
from .config import Settings
from .events import EventBus, StatusEvent
from .rag import Retriever
from .session import Session
from .stt.biasing import build_biasing_prompt
from .stt.whisper import Hypothesis, WhisperTranscriber, transcribe_stream


async def run_producer(
    settings: Settings, session: Session, bus: EventBus, retriever: Retriever | None = None
) -> None:
    """Transcribe captured audio into the session, but only while capture is active.

    Capture runs only inside an interview: the session is paused on the dashboard and
    resumed/paused as the user enters and leaves. Pausing tears the capture stream down so
    pw-record actually stops, rather than reading and discarding audio. No LLM call here:
    answers are generated on demand via a client command. Runs for the server's lifetime.
    """
    device = settings.audio_device or await get_default_monitor()
    transcriber = WhisperTranscriber(
        model_name=settings.whisper_model,
        use_gpu=settings.whisper_use_gpu,
        gpu_device=settings.whisper_gpu_device,
    )
    # Pay the one-time Vulkan shader compile now so the first real utterance is fast.
    await asyncio.get_event_loop().run_in_executor(None, transcriber.warmup)
    bus.publish(StatusEvent(state="idle"))
    while True:
        await session.wait_capture_start()
        # Refresh context-biasing vocab from current docs on each interview start (cheap, picks
        # up docs added via the wizard after boot); not per-chunk.
        if settings.stt_context_biasing and retriever is not None:
            transcriber.set_prompt(
                build_biasing_prompt(
                    retriever.store.chunk_texts(),
                    settings.stt_bias_terms,
                    settings.stt_bias_max_chars,
                )
            )
        else:
            transcriber.set_prompt("")
        bus.publish(StatusEvent(state="listening"))
        audio_stream = capture_audio(device)
        transcript_stream = transcribe_stream(
            audio_stream,
            transcriber,
            silence_rms=settings.vad_silence_rms,
            silence_hold_s=settings.vad_silence_hold_s,
            max_utterance_s=settings.vad_max_utterance_s,
            partial_interval_s=settings.stt_partial_interval_s,
            commit_margin_words=settings.stt_commit_margin_words,
            emit_partials=settings.stt_emit_partials,
        )
        feed = asyncio.ensure_future(_feed(transcript_stream, session, settings))
        stop = asyncio.ensure_future(session.wait_capture_stop())
        try:
            done, _ = await asyncio.wait({feed, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
            feed.cancel()
            await asyncio.gather(stop, feed, return_exceptions=True)
            # Close both: transcribe_stream does not finalize its source, so the capture stream
            # is closed explicitly to run capture_audio's finally and terminate pw-record.
            await transcript_stream.aclose()
            await audio_stream.aclose()
            bus.publish(StatusEvent(state="idle"))
        # Feed ending while capture was never paused means pw-record died; back off so a
        # persistent failure cannot spin this loop hot (wait_capture_start returns at once).
        if feed in done and stop not in done:
            await asyncio.sleep(1.0)


async def _feed(
    transcript_stream: AsyncIterator[Hypothesis], session: Session, settings: Settings
) -> None:
    async for item in transcript_stream:
        if item.final:
            if settings.debug_print:
                print(f"[FINAL] {item.text}")
            session.add_transcript(item.text)
        else:
            session.publish_partial(item.text)
