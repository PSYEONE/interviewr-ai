"""Transcript producer: PipeWire audio -> whisper.cpp STT -> Session/EventBus."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .audio import routing
from .audio.capture import capture_audio, get_default_sink
from .config import Settings
from .events import EventBus, StatusEvent
from .session import Session
from .stt.whisper import Hypothesis, WhisperTranscriber, transcribe_stream


@dataclass
class CapturePrefs:
    """Where to capture from, mutable so POST /settings/reload can change it with no restart.

    The producer is handed this instead of a Settings snapshot, which reload replaces wholesale
    and so could never reach a long-running task. Same idea as Retriever.set_top_k.
    """

    device: str = ""
    app: str = ""

    @classmethod
    def of(cls, settings: Settings) -> CapturePrefs:
        return cls(device=settings.audio_device, app=settings.audio_capture_app)

    def update(self, settings: Settings) -> None:
        self.device = settings.audio_device
        self.app = settings.audio_capture_app


async def resolve_device(prefs: CapturePrefs) -> str:
    """The PipeWire source to record, applying the app-isolation routing as a side effect."""
    if prefs.app:
        return await routing.reconcile(prefs.app)
    # Also tears down a null sink left over from app mode, and hands back the default monitor.
    fallback = await routing.reconcile("")
    return prefs.device or fallback


async def run_producer(
    settings: Settings, session: Session, bus: EventBus, prefs: CapturePrefs | None = None
) -> None:
    """Transcribe captured audio into the session, but only while capture is active.

    Capture runs only inside an interview: the session is paused on the dashboard and
    resumed/paused as the user enters and leaves. Pausing tears the capture stream down so
    pw-record actually stops, rather than reading and discarding audio. No LLM call here:
    answers are generated on demand via a client command. Runs for the server's lifetime.
    """
    prefs = prefs if prefs is not None else CapturePrefs.of(settings)
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
        bus.publish(StatusEvent(state="listening"))
        # Resolved per capture start, not once: it makes the capture target a live setting (no
        # engine restart), and re-routes an app that has been relaunched since the last interview,
        # which WirePlumber will not do for us.
        device = await resolve_device(prefs)
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
        # Scoped to the interview, so choosing an app in Settings never re-routes the user's
        # audio while they are just sitting on the dashboard.
        router = asyncio.ensure_future(routing.watch(prefs.app))
        try:
            done, _ = await asyncio.wait({feed, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
            feed.cancel()
            router.cancel()
            await asyncio.gather(stop, feed, router, return_exceptions=True)
            # Close both: transcribe_stream does not finalize its source, so the capture stream
            # is closed explicitly to run capture_audio's finally and terminate pw-record.
            await transcript_stream.aclose()
            await audio_stream.aclose()
            # Give the user's audio back when the interview ends. Leaving the sink loaded meant
            # our routing outlived the interview, and any app WirePlumber remembers pointing at
            # it would silently rejoin a sink nothing was capturing.
            if prefs.app:
                await routing.cleanup(await get_default_sink())
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
