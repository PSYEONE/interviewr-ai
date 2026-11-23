"""The embedder asks Ollama to stay loaded, or every answer after a quiet spell waits for a reload."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.rag.embed import OllamaEmbedder


def _capture(embedder: OllamaEmbedder, seen: list[dict[str, Any]]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    embedder._client = httpx.AsyncClient(
        base_url="http://ollama", transport=httpx.MockTransport(handler)
    )


async def test_embed_requests_carry_the_keep_alive() -> None:
    # Measured: with nothing sent, Ollama dropped nomic-embed-text after 5 minutes and the next
    # answer spent 48 s on /api/embed while the chat model, which does send it, was still resident.
    seen: list[dict[str, Any]] = []
    embedder = OllamaEmbedder("http://ollama", "nomic-embed-text", keep_alive="30m")
    _capture(embedder, seen)
    await embedder.embed(["какъв е опитът ви"])
    assert seen[0]["keep_alive"] == "30m"
    assert seen[0]["model"] == "nomic-embed-text"


async def test_keep_alive_can_be_changed_live() -> None:
    seen: list[dict[str, Any]] = []
    embedder = OllamaEmbedder("http://ollama", "nomic-embed-text")
    _capture(embedder, seen)
    embedder.set_keep_alive("1h")
    await embedder.embed(["x"])
    assert seen[0]["keep_alive"] == "1h"
