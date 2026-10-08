"""The speech model gives its GPU memory back between interviews and reloads when one starts."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest

from app import pipeline
from app.audio import routing
from app.config import Settings
from app.events import EventBus
from app.stt.whisper import WhisperTranscriber


def _settings() -> Settings:
    return Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]


class _Loaded(WhisperTranscriber):
    """Holds a stand-in model, so the release path is testable without whisper.cpp."""

    def __init__(self) -> None:
        self.on_gpu = True
        self._lock = threading.Lock()
        self._model = object()  # type: ignore[assignment]


def test_transcribing_after_release_fails_loudly() -> None:
    transcriber = _Loaded()
    transcriber.release()
    with pytest.raises(RuntimeError, match="released"):
        transcriber.transcribe_raw(np.zeros(16000, dtype=np.float32))


def test_releasing_twice_is_harmless() -> None:
    transcriber = _Loaded()
    transcriber.release()
    transcriber.release()


class _NoInterview:
    """A session that starts an interview only when the test says so."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def wait_capture_start(self) -> None:
        await self.started.wait()

    async def wait_capture_stop(self) -> None:
        await asyncio.Event().wait()


class _Harness:
    """Runs the producer against fakes, recording every transcriber built and released."""

    def __init__(self) -> None:
        self.built: list[Any] = []
        self.released = asyncio.Event()
        self.reloaded = asyncio.Event()
        self.states: list[tuple[bool, int]] = []
        self.session = _NoInterview()

    def transcriber(self, **kwargs: object) -> Any:
        harness = self

        class _FakeTranscriber:
            on_gpu = bool(kwargs.get("use_gpu"))

            def warmup(self) -> None:
                pass

            def release(self) -> None:
                harness.released.set()

        fake = _FakeTranscriber()
        self.built.append(fake)
        return fake

    def record(self, ready: bool, percent: int) -> None:
        self.states.append((ready, percent))
        if ready and len(self.built) == 2:
            self.reloaded.set()


def _patch(monkeypatch: pytest.MonkeyPatch, harness: _Harness, gpu_has_room: bool) -> None:
    async def has_room(*_args: object) -> bool:
        return gpu_has_room

    async def resolve(_prefs: pipeline.CapturePrefs) -> str:
        return "sink.monitor"

    async def no_audio(*_args: object, **_kwargs: object) -> AsyncIterator[Any]:
        return
        yield

    async def idle_watch(_app: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(pipeline, "WhisperTranscriber", harness.transcriber)
    monkeypatch.setattr(pipeline, "ensure_model", lambda *_args: None)  # no model download in tests
    monkeypatch.setattr(pipeline, "_IDLE_RELEASE_S", 0.05)
    monkeypatch.setattr(pipeline, "_gpu_has_room", has_room)
    monkeypatch.setattr(pipeline, "resolve_device", resolve)
    monkeypatch.setattr(pipeline, "capture_audio", no_audio)
    monkeypatch.setattr(pipeline, "transcribe_stream", no_audio)
    monkeypatch.setattr(routing, "watch", idle_watch)


@contextlib.asynccontextmanager
async def _producer(harness: _Harness) -> AsyncIterator[None]:
    task = asyncio.create_task(
        pipeline.run_producer(
            _settings(),
            harness.session,  # type: ignore[arg-type]
            EventBus(),
            None,
            harness.record,
        )
    )
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_idle_releases_the_model_and_the_next_interview_reloads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    harness = _Harness()
    _patch(monkeypatch, harness, gpu_has_room=True)
    caplog.set_level("INFO", logger="app")

    async with _producer(harness):
        await asyncio.wait_for(harness.released.wait(), 5)
        assert (False, 0) in harness.states  # the interview view can explain the wait
        assert "released the speech model" in caplog.text

        harness.session.started.set()
        await asyncio.wait_for(harness.reloaded.wait(), 5)
        assert harness.states[-1] == (True, 100)


async def test_a_cpu_model_is_kept_because_releasing_it_frees_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness()
    _patch(monkeypatch, harness, gpu_has_room=False)

    async with _producer(harness):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(harness.released.wait(), 0.3)  # six idle windows
        assert len(harness.built) == 1
