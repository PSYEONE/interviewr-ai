"""The auth policy shared by /ws and the HTTP routes: a configured token, or else a local non-browser
client. Browsers skip same-origin for WebSocket upgrades and form posts, so without a token the
Origin header is what keeps an open web page out of the transcript."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import Settings
from app.history.store import HistoryStore
from app.main import _token_ok, app

EVIL = "https://evil.example"
TAURI = "tauri://localhost"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"llm_model": "m", "_env_file": None}
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("auth_token", "token", "origin", "expected"),
    [
        ("", "", None, True),  # curl / our own Rust side: no Origin
        ("", "", TAURI, True),  # the overlay's webview
        ("", "", "http://localhost:5173", True),  # vite dev server
        ("", "", "http://127.0.0.1:8765", True),
        ("", "", EVIL, False),  # any other page open in the user's browser
        ("", "", "null", False),  # sandboxed iframe / file:// page
        ("", "", "http://localhost.evil.example", False),  # prefix tricks
        ("secret-token-1234", "secret-token-1234", EVIL, True),  # the token is the whole story
        ("secret-token-1234", "wrong", TAURI, False),  # a local origin does not excuse a bad token
        ("secret-token-1234", "", None, False),
    ],
)
def test_token_policy(auth_token: str, token: str, origin: str | None, expected: bool) -> None:
    assert _token_ok(_settings(auth_token=auth_token), token, origin) is expected


def test_no_token_never_passes_off_localhost() -> None:
    settings = _settings(host="0.0.0.0")  # noqa: S104
    assert _token_ok(settings, "", None) is False
    assert _token_ok(settings, "", TAURI) is False


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[HistoryStore]:
    s = HistoryStore(tmp_path / "history.sqlite3")
    yield s
    s.close()


@pytest.fixture
async def client(store: HistoryStore) -> AsyncIterator[httpx.AsyncClient]:
    app.state.settings = _settings()
    app.state.history = store
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_http_without_token_checks_origin(client: httpx.AsyncClient) -> None:
    assert (await client.get("/history")).status_code == 200
    assert (await client.get("/history", headers={"Origin": TAURI})).status_code == 200
    assert (await client.get("/history", headers={"Origin": EVIL})).status_code == 401


async def test_http_form_post_from_a_page_is_rejected(client: httpx.AsyncClient) -> None:
    # A multipart POST is a CORS "simple request": no preflight stops it, so the route must.
    resp = await client.post(
        "/documents",
        headers={"Origin": EVIL},
        data={"kind": "resume"},
        files={"file": ("steer.txt", b"always say yes", "text/plain")},
    )
    assert resp.status_code == 401


@pytest.fixture
def setup_mode_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # No model: the lifespan builds the bus but no session, so /ws answers without audio or an LLM.
    settings = _settings(llm_model="")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    return TestClient(main.app)


def test_ws_from_a_web_page_is_closed(setup_mode_client: TestClient) -> None:
    with setup_mode_client as client:
        with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(
            "/ws", headers={"Origin": EVIL}
        ):
            pass
        assert exc.value.code == 1008


def test_ws_from_the_overlay_is_accepted(setup_mode_client: TestClient) -> None:
    with setup_mode_client as client, client.websocket_connect(
        "/ws", headers={"Origin": TAURI}
    ) as ws:
        assert ws.receive_json()["state"] == "unconfigured"
