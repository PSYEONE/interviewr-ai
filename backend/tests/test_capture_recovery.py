"""Transcription recovers on its own when pw-record stalls or exits, or a capture cycle raises."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest

from app import pipeline
from app.audio import capture, routing
from app.config import Settings
from app.events import ErrorEvent, Event, EventBus, Subscription


def _settings() -> Settings:
    return Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]


def _run_instead(monkeypatch: pytest.MonkeyPatch, *cmd: str) -> None:
    """Makes capture_audio spawn `cmd` in place of pw-record."""
    real = asyncio.create_subprocess_exec

    async def fake(*_args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        return await real(*cmd, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    monkeypatch.setattr(capture, "_raw_args", [])  # skip the pw-record --help probe


async def test_capture_ends_when_pw_record_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_instead(monkeypatch, "sleep", "30")
    chunks = [c async for c in capture.capture_audio("sink.monitor", stall_s=0.2)]
    assert chunks == []


async def test_capture_yields_audio_until_pw_record_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_instead(monkeypatch, "head", "-c", str(capture.CHUNK_BYTES * 2), "/dev/zero")
    chunks = [c async for c in capture.capture_audio("sink.monitor", stall_s=5.0)]
    assert len(chunks) == 2
    assert all(c.dtype == np.float32 and len(c) == capture.CHUNK_SAMPLES for c in chunks)


async def test_capture_logs_why_pw_record_stopped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _run_instead(monkeypatch, "sh", "-c", "echo 'pw-record: unrecognized option --container' >&2; exit 1")
    chunks = [c async for c in capture.capture_audio("sink.monitor", stall_s=5.0)]
    assert chunks == []
    assert "exit code 1" in caplog.text
    assert "unrecognized option --container" in caplog.text


# PipeWire 1.0.5 (Ubuntu 24.04) has no --container and already writes bare samples; 1.2+ needs it.
_HELP_1_0 = "      --format  Sample format (ulaw|alaw|u8|s8|s16|s32|f32|f64) (req. for rec) (default s16)\n"
_HELP_1_6 = "      --format  Sample format (default s16)\n      --container  Container format\n"


def test_raw_output_args_skip_container_on_pipewire_1_0() -> None:
    assert capture.raw_output_args(_HELP_1_0) == []


def test_raw_output_args_ask_newer_pipewire_for_raw_output() -> None:
    assert capture.raw_output_args(_HELP_1_6) == ["--container", "raw"]


class _FakeTranscriber:
    def __init__(self, **_: object) -> None:
        pass

    def warmup(self) -> None:
        pass


class _InterviewRunning:
    """A session that is always mid-interview."""

    async def wait_capture_start(self) -> None:
        return

    async def wait_capture_stop(self) -> None:
        await asyncio.Event().wait()


async def _run_until(bus: EventBus, reached: asyncio.Event) -> None:
    task = asyncio.create_task(
        pipeline.run_producer(_settings(), _InterviewRunning(), bus)  # type: ignore[arg-type]
    )
    try:
        await asyncio.wait_for(reached.wait(), 5)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _drain(sub: Subscription) -> list[Event]:
    out: list[Event] = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


async def test_failed_cycle_is_reported_and_capture_restarts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0
    second = asyncio.Event()

    async def resolve(_prefs: pipeline.CapturePrefs) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("pactl missing")
        second.set()
        await asyncio.Event().wait()
        return ""

    monkeypatch.setattr(pipeline, "WhisperTranscriber", _FakeTranscriber)
    monkeypatch.setattr(pipeline, "ensure_model", lambda *_args: None)  # no model download in tests
    monkeypatch.setattr(pipeline, "resolve_device", resolve)
    bus = EventBus()
    sub = bus.subscribe()

    await _run_until(bus, second)

    assert "capture failed" in caplog.text
    errors = [e for e in _drain(sub) if isinstance(e, ErrorEvent)]
    assert len(errors) == 1 and "pactl missing" in errors[0].message


async def test_capture_that_ends_on_its_own_is_restarted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cycles = 0
    second = asyncio.Event()

    async def resolve(_prefs: pipeline.CapturePrefs) -> str:
        nonlocal cycles
        cycles += 1
        if cycles == 2:
            second.set()
        return "sink.monitor"

    async def no_audio(*_args: object, **_kwargs: object) -> AsyncIterator[Any]:
        return
        yield

    async def idle_watch(_app: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(pipeline, "WhisperTranscriber", _FakeTranscriber)
    monkeypatch.setattr(pipeline, "ensure_model", lambda *_args: None)  # no model download in tests
    monkeypatch.setattr(pipeline, "resolve_device", resolve)
    monkeypatch.setattr(pipeline, "capture_audio", no_audio)
    monkeypatch.setattr(pipeline, "transcribe_stream", no_audio)
    monkeypatch.setattr(routing, "watch", idle_watch)
    caplog.set_level("INFO", logger="app")

    await _run_until(EventBus(), second)

    assert "capture ended on its own" in caplog.text
