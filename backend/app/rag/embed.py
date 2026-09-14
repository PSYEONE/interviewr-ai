"""Embedding backends and factory: Ollama by default, or OpenAI or Gemini with an API key.

Anthropic has no embeddings API, so it isn't offered.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import httpx

from ..config import EmbedProvider
from .base import Embedder

if TYPE_CHECKING:
    from ..config import Settings


class OllamaEmbedder:
    """Local embeddings via Ollama's ``/api/embed`` (batched ``input``)."""

    def __init__(self, base_url: str, model: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._model = model

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    async def check_health(self) -> bool:
        try:
            resp = await self._client.get("/api/version")
            return resp.status_code == 200
        except httpx.ConnectError:
            return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.post(
            "/api/embed", json={"model": self._model, "input": list(texts)}
        )
        resp.raise_for_status()
        data = resp.json()
        return [[float(x) for x in vec] for vec in data["embeddings"]]

    async def close(self) -> None:
        await self._client.aclose()


class OpenAIEmbedder:
    """OpenAI (or any OpenAI-compatible) embeddings endpoint."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        from openai import AsyncOpenAI  # lazy: cloud extra

        self._client: Any = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    async def check_health(self) -> bool:
        from openai import APIError

        try:
            await self._client.models.retrieve(self._model)
            return True
        except APIError:
            return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.embeddings.create(model=self._model, input=list(texts))
        return [list(item.embedding) for item in resp.data]

    async def close(self) -> None:
        await self._client.close()


class GeminiEmbedder:
    """Gemini embeddings via Google AI Studio keys (``embed_content``)."""

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai  # lazy: cloud extra

        self._client: Any = genai.Client(api_key=api_key)
        self._model = model

    @property
    def model_id(self) -> str:
        return f"gemini:{self._model}"

    async def check_health(self) -> bool:
        from google.genai import errors

        try:
            await self._client.aio.models.get(model=self._model)
            return True
        except errors.APIError:
            return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.aio.models.embed_content(
            model=self._model, contents=list(texts)
        )
        return [list(e.values) for e in resp.embeddings]

    async def close(self) -> None:
        aio = getattr(self._client, "aio", None)
        api_client = getattr(aio, "_api_client", None)
        httpx_client = getattr(api_client, "_async_httpx_client", None)
        if httpx_client is not None:
            await httpx_client.aclose()


def build_embedder(settings: Settings) -> Embedder:
    match settings.embed_provider:
        case EmbedProvider.OLLAMA:
            return OllamaEmbedder(settings.ollama_base_url, settings.embed_model)
        case EmbedProvider.OPENAI:
            return OpenAIEmbedder(settings.openai_api_key, settings.embed_model)
        case EmbedProvider.GEMINI:
            return GeminiEmbedder(settings.gemini_api_key, settings.embed_model)


__all__ = [
    "GeminiEmbedder",
    "OllamaEmbedder",
    "OpenAIEmbedder",
    "build_embedder",
]
