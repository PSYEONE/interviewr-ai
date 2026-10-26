"""A slow transcription pass must never stall pw-record: audio is queued, and the oldest is dropped
once the queue is full. On the CPU fallback the live partials that cause the slowness are off."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest

from app import pipeline
from app.audio import capture, routing
from app.config import Settings
from app.events import EventBus
from app.stt.whisper import Hypothesis


def _reader_with(chunks: list[bytes]) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for c in chunks:
        reader.feed_data(c)
    reader.feed_eof()
    return reader


async def test_pump_keeps_the_newest_audio_when_the_consumer_is_slow() -> None:
    chunks = [bytes([i]) * 4 for i in range(10)]
    pump = capture.AudioPump(_reader_with(chunks), chunk_bytes=4, max_chunks=3)
    await pump.run()  # nobody reads while it runs: the worst case
    got = []
    while (item := pump.queue.get_nowait()) is not None:
        got.append(item)
    # The EOF marker displaced one more chunk, so the newest two survive.
    assert got == chunks[-2:]
    assert pump.dropped == 8


async def test_pump_passes_everything_through_when_the_consumer_keeps_up() -> None:
    chunks = [bytes([i]) * 4 for i in range(5)]
    pump = capture.AudioPump(_reader_with(chunks), chunk_bytes=4, max_chunks=100)
    await pump.run()
    got = [pump.queue.get_nowait() for _ in range(5)]
    assert got == chunks
    assert pump.queue.get_nowait() is None
    assert pump.dropped == 0


async def test_pump_logs_the_first_drop(caplog: pytest.LogCaptureFixture) -> None:
    pump = capture.AudioPump(_reader_with([b"abcd"] * 4), chunk_bytes=4, max_chunks=1)
    await pump.run()
    assert "dropped 1 audio chunks" in caplog.text


async def test_pump_ignores_a_trailing_partial_chunk() -> None:
    pump = capture.AudioPump(_reader_with([b"abcd", b"ab"]), chunk_bytes=4, max_chunks=10)
    await pump.run()
    assert pump.queue.get_nowait() == b"abcd"
    assert pump.queue.get_nowait() is None


class _Transcriber:
    def __init__(self, on_gpu: bool) -> None:
        self.on_gpu = on_gpu


class _Session:
    async def wait_capture_stop(self) -> None:
        await asyncio.Event().wait()

    def add_transcript(self, text: str) -> None:
        pass

    def publish_partial(self, text: str) -> None:
        pass


async def _capture_with(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, on_gpu: bool
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def no_audio() -> AsyncIterator[np.ndarray]:
        return
        yield  # pragma: no cover

    def fake_transcribe(*_args: Any, **kwargs: Any) -> AsyncIterator[Hypothesis]:
        seen.update(kwargs)

        async def empty() -> AsyncIterator[Hypothesis]:
            return
            yield  # pragma: no cover

        return empty()

    async def resolve(_prefs: pipeline.CapturePrefs) -> str:
        return "sink.monitor"

    async def no_watch(_app: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(pipeline, "capture_audio", lambda _device: no_audio())
    monkeypatch.setattr(pipeline, "transcribe_stream", fake_transcribe)
    monkeypatch.setattr(pipeline, "resolve_device", resolve)
    monkeypatch.setattr(routing, "watch", no_watch)
    prefs = pipeline.CapturePrefs.of(settings)
    restart = await pipeline._capture_once(
        settings, _Session(), EventBus(), prefs, _Transcriber(on_gpu)  # type: ignore[arg-type]
    )
    assert restart is True  # the empty stream ended the cycle on its own
    return seen


async def test_partials_stay_on_for_the_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(llm_model="m", stt_emit_partials=True, _env_file=None)  # type: ignore[call-arg]
    assert (await _capture_with(monkeypatch, settings, on_gpu=True))["emit_partials"] is True


async def test_partials_turn_off_on_the_cpu_fallback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(llm_model="m", stt_emit_partials=True, _env_file=None)  # type: ignore[call-arg]
    with caplog.at_level("INFO", logger="app.pipeline"):
        assert (await _capture_with(monkeypatch, settings, on_gpu=False))["emit_partials"] is False
    assert "live partials are off" in caplog.text


async def test_partials_off_in_config_stay_off_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(llm_model="m", stt_emit_partials=False, _env_file=None)  # type: ignore[call-arg]
    with caplog.at_level("INFO", logger="app.pipeline"):
        assert (await _capture_with(monkeypatch, settings, on_gpu=False))["emit_partials"] is False
    assert "live partials are off" not in caplog.text
