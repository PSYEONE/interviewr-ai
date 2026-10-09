"""Setup mode: with no llm_model the engine boots degraded but still serves the wizard's
capability/settings endpoints, and /health reports it as unconfigured."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings


@pytest.fixture
def setup_mode_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Force an unconfigured config regardless of the developer's real .env. No model means the
    # lifespan skips the LLM/producer entirely, so this needs no Ollama or audio device.
    overrides: dict[str, object] = {"llm_model": "", "_env_file": None}
    settings = Settings(**overrides)  # type: ignore[arg-type]
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    return TestClient(main.app)


def test_health_reports_unconfigured(setup_mode_client: TestClient) -> None:
    with setup_mode_client as client:
        body = client.get("/health").json()
    assert body["configured"] is False
    assert body["ready"] is False
    assert body["status"] == "ok"


def test_health_says_the_model_cannot_reason_until_proven_otherwise(
    setup_mode_client: TestClient,
) -> None:
    # The interview screen hides its reasoning toggle on this, so the safe answer is False.
    with setup_mode_client as client:
        assert client.get("/health").json()["llm_can_think"] is False


def test_settings_endpoint_live_in_setup_mode(setup_mode_client: TestClient) -> None:
    # The capability surface the wizard drives must answer even with no model configured.
    with setup_mode_client as client:
        resp = client.get("/settings")
    assert resp.status_code == 200
    assert resp.json()["llm_model"] == ""
