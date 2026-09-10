"""CapturePrefs: the capture target is resolved per interview, not once at engine start."""

from __future__ import annotations

import pytest

from app import pipeline
from app.config import Settings
from app.pipeline import CapturePrefs, resolve_device


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"llm_model": "m", "_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_prefs_read_both_capture_fields() -> None:
    prefs = CapturePrefs.of(_settings(audio_device="mic.x", audio_capture_app="Chromium"))
    assert (prefs.device, prefs.app) == ("mic.x", "Chromium")


def test_update_mutates_in_place() -> None:
    # In place matters: the producer holds this object for the life of the process, so replacing
    # it would leave the running task on the old target.
    prefs = CapturePrefs()
    prefs.update(_settings(audio_capture_app="Chromium"))
    assert prefs.app == "Chromium"


async def test_isolating_an_app_captures_its_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_reconcile(app: str) -> str:
        seen.append(app)
        return "interviewr.monitor" if app else "default.monitor"

    monkeypatch.setattr(pipeline.routing, "reconcile", fake_reconcile)
    assert await resolve_device(CapturePrefs(app="Chromium")) == "interviewr.monitor"
    assert seen == ["Chromium"]


async def test_no_app_tears_the_sink_down_and_uses_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching back to "everything" must unload the null sink, not just stop capturing it,
    or the user's audio stays routed into a sink nothing is listening to."""
    seen: list[str] = []

    async def fake_reconcile(app: str) -> str:
        seen.append(app)
        return "default.monitor"

    monkeypatch.setattr(pipeline.routing, "reconcile", fake_reconcile)
    assert await resolve_device(CapturePrefs()) == "default.monitor"
    assert seen == [""]  # called with "", which is the teardown path


async def test_an_explicit_device_still_wins_over_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_reconcile(app: str) -> str:
        return "default.monitor"

    monkeypatch.setattr(pipeline.routing, "reconcile", fake_reconcile)
    assert await resolve_device(CapturePrefs(device="Mic1")) == "Mic1"
