"""POST /ollama/delete over the ASGI app: the in-use guards, plus 404 and success.

Injects app.state.settings directly and uses an ASGI transport, which skips the lifespan
(no LLM, producer or audio). OllamaAdmin is monkeypatched because the route builds its own
and gives no way to inject a mock transport.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from app import main
from app.config import Settings
from app.main import app


class FakeAdmin:
    """Records what the route asked to delete; knows only the models it was seeded with."""

    deleted: list[str] = []

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def delete_model(self, model: str) -> bool:
        FakeAdmin.deleted.append(model)
        return model == "qwen2.5:7b"

    async def close(self) -> None:
        return None


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "llm_provider": "ollama",
        "llm_model": "llama3.1:8b",
        "embed_provider": "ollama",
        "embed_model": "nomic-embed-text",
        "rag_enabled": True,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def fake_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAdmin.deleted = []
    monkeypatch.setattr(main, "OllamaAdmin", FakeAdmin)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app.state.settings = _settings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_deletes_an_unused_model(client: httpx.AsyncClient) -> None:
    resp = await client.post("/ollama/delete", json={"model": "qwen2.5:7b"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert FakeAdmin.deleted == ["qwen2.5:7b"]


async def test_missing_model_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/ollama/delete", json={"model": "never-pulled:1b"})
    assert resp.status_code == 404


async def test_refuses_the_answering_model(client: httpx.AsyncClient) -> None:
    resp = await client.post("/ollama/delete", json={"model": "llama3.1:8b"})
    assert resp.status_code == 409
    assert FakeAdmin.deleted == []  # guarded before Ollama is ever called


async def test_refuses_the_answering_model_across_latest(client: httpx.AsyncClient) -> None:
    """.env may hold a bare tag while the UI sends the `:latest` Ollama reports."""
    app.state.settings = _settings(llm_model="phi4")
    resp = await client.post("/ollama/delete", json={"model": "phi4:latest"})
    assert resp.status_code == 409
    assert FakeAdmin.deleted == []


async def test_refuses_the_embed_model_while_rag_is_on(client: httpx.AsyncClient) -> None:
    resp = await client.post("/ollama/delete", json={"model": "nomic-embed-text"})
    assert resp.status_code == 409
    assert FakeAdmin.deleted == []


async def test_allows_the_embed_model_once_rag_is_off(client: httpx.AsyncClient) -> None:
    app.state.settings = _settings(rag_enabled=False, embed_model="qwen2.5:7b")
    resp = await client.post("/ollama/delete", json={"model": "qwen2.5:7b"})
    assert resp.status_code == 200


async def test_cloud_llm_does_not_guard_a_local_tag(client: httpx.AsyncClient) -> None:
    """The llm_model guard is only meaningful when the provider is Ollama."""
    app.state.settings = _settings(
        llm_provider="anthropic",
        llm_model="qwen2.5:7b",
        anthropic_api_key="test-key",  # the provider refuses to configure without one
        rag_enabled=False,
    )
    resp = await client.post("/ollama/delete", json={"model": "qwen2.5:7b"})
    assert resp.status_code == 200
