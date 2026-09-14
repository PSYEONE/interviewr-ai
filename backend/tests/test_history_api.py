"""/history endpoints with an injected store; app.state is set directly so no LLM or audio starts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.history.store import HistoryStore
from app.main import app


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[HistoryStore]:
    s = HistoryStore(tmp_path / "history.sqlite3")
    yield s
    s.close()


@pytest.fixture
async def client(store: HistoryStore) -> AsyncIterator[httpx.AsyncClient]:
    app.state.settings = Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]
    app.state.history = store
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed(store: HistoryStore) -> int:
    cid = store.start_conversation()
    store.add_turn(cid, "Tell me about yourself", "I build Linux tools.")
    store.add_turn(cid, "Why this role?", "Mission fit.")
    return cid


async def test_list_and_get(client: httpx.AsyncClient, store: HistoryStore) -> None:
    cid = _seed(store)
    listed = (await client.get("/history")).json()
    assert len(listed) == 1
    assert listed[0]["turn_count"] == 2
    assert listed[0]["preview"] == "Tell me about yourself"

    detail = (await client.get(f"/history/{cid}")).json()
    assert [t["question"] for t in detail["turns"]] == ["Tell me about yourself", "Why this role?"]
    assert (await client.get("/history/9999")).status_code == 404


async def test_delete_and_clear(client: httpx.AsyncClient, store: HistoryStore) -> None:
    cid = _seed(store)
    assert (await client.delete(f"/history/{cid}")).json() == {"deleted": True}
    assert (await client.delete(f"/history/{cid}")).status_code == 404
    _seed(store)
    assert (await client.delete("/history")).json() == {"cleared": True}
    assert (await client.get("/history")).json() == []


async def test_export_markdown_and_json(client: httpx.AsyncClient, store: HistoryStore) -> None:
    cid = _seed(store)
    md = await client.get(f"/history/{cid}/export")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert "## Q: Tell me about yourself" in md.text

    js = await client.get(f"/history/{cid}/export", params={"format": "json"})
    assert js.status_code == 200
    assert js.json()["turns"][0]["answer"] == "I build Linux tools."

    all_md = await client.get("/history/export")
    assert all_md.status_code == 200
    assert "## Q: Why this role?" in all_md.text


async def test_auth_required(store: HistoryStore) -> None:
    app.state.settings = Settings(  # type: ignore[call-arg]
        llm_model="m", auth_token="secret-token-1234", _env_file=None
    )
    app.state.history = store
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/history")).status_code == 401
        ok = await c.get("/history", headers={"Authorization": "Bearer secret-token-1234"})
        assert ok.status_code == 200
