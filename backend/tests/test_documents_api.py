"""HTTP /documents endpoints over the ASGI app, with a fake embedder (no Ollama, no audio).

Drives the real routing + Retriever + DocumentStore by injecting app.state directly and
using an ASGI transport, which skips the lifespan (so no LLM/producer/audio is started).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import app
from app.rag.retriever import Retriever
from app.rag.store import DocumentStore


class FakeEmbedder:
    """Deterministic 8-dim embeddings from a text hash. No network."""

    model_id = "fake:v1"

    async def check_health(self) -> bool:
        return True

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode()).digest()[:8]
            out.append([b / 255.0 for b in digest])
        return out

    async def close(self) -> None:
        return None


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    retriever = Retriever(
        FakeEmbedder(),
        DocumentStore(tmp_path / "rag.sqlite3"),
        top_k=3,
        min_score=0.0,
        chunk_chars=200,
        chunk_overlap=20,
    )
    app.state.settings = Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]
    app.state.retriever = retriever
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await retriever.close()


async def test_upload_text_then_list_then_delete(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/documents", data={"kind": "job_description", "name": "jd", "text": "Senior Python role"}
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["kind"] == "job_description"
    assert record["chunk_count"] >= 1

    listed = (await client.get("/documents")).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "jd"

    doc_id = record["id"]
    assert (await client.delete(f"/documents/{doc_id}")).json() == {"deleted": True}
    assert (await client.get("/documents")).json() == []
    assert (await client.delete(f"/documents/{doc_id}")).status_code == 404


async def test_upload_file(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/documents",
        data={"kind": "resume"},
        files={"file": ("resume.txt", b"Built a Linux audio pipeline in Rust.", "text/plain")},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "resume.txt"


async def test_unsupported_file_is_400(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/documents",
        data={"kind": "other"},
        files={"file": ("notes.rtf", b"x", "application/rtf")},
    )
    assert resp.status_code == 400
    assert "unsupported" in resp.json()["detail"]


async def test_missing_payload_is_400(client: httpx.AsyncClient) -> None:
    resp = await client.post("/documents", data={"kind": "other"})
    assert resp.status_code == 400
