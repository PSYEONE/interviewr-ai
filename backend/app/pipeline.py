"""Transcript producer: PipeWire audio -> whisper.cpp STT -> Session/EventBus."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from .audio import routing
from .audio.capture import capture_audio, get_default_sink
from .config import Settings
from .events import ErrorEvent, EventBus, StatusEvent
from .session import Session
from .stt.whisper import (
    Hypothesis,
    WhisperTranscriber,
    ensure_model,
    fits_in_vram,
    transcribe_stream,
)
from .system.hardware import free_vram_mb
from .system.whisper_models import vram_needed_mb

_log = logging.getLogger(__name__)

# Wait before trying the speech model again after a failed download.
_MODEL_RETRY_S = 30.0


@dataclass
class CapturePrefs:
    """Capture target, updated in place by POST /settings/reload so the running producer sees changes."""

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
    # Also removes any leftover null sink from app mode and returns the default monitor.
    fallback = await routing.reconcile("")
    return prefs.device or fallback


def _build_transcriber(
    settings: Settings, use_gpu: bool, on_progress: Callable[[int], None] | None
) -> WhisperTranscriber:
    """Downloads the speech model if needed and loads it. Blocking, so it runs in a thread."""
    ensure_model(settings.whisper_model, on_progress)
    transcriber = WhisperTranscriber(
        model_name=settings.whisper_model,
        use_gpu=use_gpu,
        gpu_device=settings.whisper_gpu_device,
    )
    # Pay the one-time Vulkan shader compile now so the first real utterance is fast.
    transcriber.warmup()
    return transcriber


async def _prepare_transcriber(
    settings: Settings, bus: EventBus, on_state: Callable[[bool, int], None] | None
) -> WhisperTranscriber:
    """Keeps trying to get the speech model ready, reporting failures instead of ending transcription."""
    progress = (lambda pct: on_state(False, pct)) if on_state is not None else None
    while True:
        try:
            use_gpu = settings.whisper_use_gpu and await _gpu_has_room(settings, bus)
            return await asyncio.to_thread(_build_transcriber, settings, use_gpu, progress)
        except Exception as exc:  # noqa: BLE001 - usually no internet; the user can fix it and we retry
            _log.exception("could not prepare the speech model")
            bus.publish(ErrorEvent(code="internal", message=str(exc)))
            await asyncio.sleep(_MODEL_RETRY_S)


async def _gpu_has_room(settings: Settings, bus: EventBus) -> bool:
    """Whether the speech model fits in free VRAM. The LLM shares the card, and a tight fit crashes."""
    needed_mb = vram_needed_mb(settings.whisper_model)
    free_mb = await asyncio.to_thread(free_vram_mb)
    if needed_mb is None or fits_in_vram(needed_mb, free_mb):
        return True
    _log.warning(
        "only %s MB of GPU memory free but %s needs about %s MB; transcribing on the CPU",
        free_mb,
        settings.whisper_model,
        needed_mb,
    )
    bus.publish(
        ErrorEvent(
            code="internal",
            message=(
                f"Not enough free GPU memory for {settings.whisper_model} ({free_mb} MB free). "
                "Transcribing on the CPU, which is slower. Close other GPU apps, or pick a smaller "
                "speech model in Settings."
            ),
        )
    )
    return False


async def run_producer(
    settings: Settings,
    session: Session,
    bus: EventBus,
    prefs: CapturePrefs | None = None,
    on_state: Callable[[bool, int], None] | None = None,
) -> None:
    """Transcribes captured audio into the session while an interview is active; runs for the server's lifetime."""
    prefs = prefs if prefs is not None else CapturePrefs.of(settings)
    # In a thread: a first run downloads the model, which would otherwise block every request.
    transcriber = await _prepare_transcriber(settings, bus, on_state)
    if on_state is not None:
        on_state(True, 100)
    bus.publish(StatusEvent(state="idle"))
    failing = False
    while True:
        await session.wait_capture_start()
        try:
            retry = await _capture_once(settings, session, bus, prefs, transcriber)
            failing = False
        except Exception as exc:  # noqa: BLE001 - one broken cycle must not end transcription for the session
            _log.exception("capture failed; retrying")
            if not failing:  # tell the UI once per failure streak, not every retry
                bus.publish(ErrorEvent(code="internal", message=f"audio capture failed: {exc}"))
            failing = True
            retry = True
        if retry:
            await asyncio.sleep(1.0)


async def _capture_once(
    settings: Settings,
    session: Session,
    bus: EventBus,
    prefs: CapturePrefs,
    transcriber: WhisperTranscriber,
) -> bool:
    """Captures and transcribes until the interview pauses; returns True if capture ended on its own."""
    # Resolved at each capture start, so setting changes apply and relaunched apps get re-routed.
    device = await resolve_device(prefs)
    source = prefs.app or "system audio"
    _log.info("capturing %s (device %s)", source, device)
    bus.publish(StatusEvent(state="listening", source=source))
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
    # Only route app audio during an interview, not while the user is on the dashboard.
    router = asyncio.ensure_future(routing.watch(prefs.app))
    try:
        done, _ = await asyncio.wait({feed, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.cancel()
        feed.cancel()
        router.cancel()
        await asyncio.gather(stop, feed, router, return_exceptions=True)
        # Close both streams explicitly so pw-record is terminated.
        await transcript_stream.aclose()
        await audio_stream.aclose()
        # Hand the user's audio back to the default sink when the interview ends.
        if prefs.app:
            await routing.cleanup(await get_default_sink())
        bus.publish(StatusEvent(state="idle"))
    if feed in done and stop not in done:
        # Capture ended without a pause: pw-record exited or stalled, or transcription raised.
        exc = None if feed.cancelled() else feed.exception()
        if exc is not None:
            raise exc
        _log.warning("capture ended on its own; restarting")
        return True
    return False


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
