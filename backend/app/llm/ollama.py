"""Ollama streaming LLM client (stateless)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Literal, TypedDict

import httpx

SYSTEM_PROMPT = (
    "You are a real-time meeting and interview copilot. "
    "You receive live audio transcription and provide concise, helpful responses. "
    "Focus on answering questions detected in the transcript, "
    "providing relevant context, and surfacing key points. "
    "Be direct and brief."
)


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class OllamaClient:
    """Stateless Ollama client. Conversation history lives in the Session."""

    def __init__(self, base_url: str, model: str, keep_alive: str = "30m") -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._model = model
        self._keep_alive = keep_alive

    async def check_health(self) -> bool:
        try:
            resp = await self._client.get("/api/version")
            return resp.status_code == 200
        except httpx.ConnectError:
            return False

    async def warmup(self) -> None:
        """Load the model into memory so the first real response has no cold-start lag."""
        resp = await self._client.post(
            "/api/chat",
            json={
                "model": self._model,
                "stream": False,
                "keep_alive": self._keep_alive,
                "messages": [{"role": "user", "content": "ok"}],
            },
        )
        resp.raise_for_status()

    async def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Stream tokens for the given conversation. SYSTEM_PROMPT is prepended."""
        payload: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
        payload.extend(messages)
        async with self._client.stream(
            "POST",
            "/api/chat",
            json={
                "model": self._model,
                "stream": True,
                "keep_alive": self._keep_alive,
                "messages": payload,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                token = data.get("message", {}).get("content", "")
                if token:
                    yield token
                if data.get("done", False):
                    break

    async def close(self) -> None:
        await self._client.aclose()
