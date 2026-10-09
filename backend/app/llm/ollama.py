"""Ollama streaming LLM client (stateless)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import httpx

from .base import SYSTEM_PROMPT, Chunk, Message


class OllamaClient:
    """Stateless Ollama client. Conversation history lives in the Session."""

    def __init__(
        self,
        base_url: str,
        model: str,
        keep_alive: str = "30m",
        system_prompt: str = SYSTEM_PROMPT,
        *,
        transport: httpx.AsyncBaseTransport | None = None,  # for tests
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0, transport=transport)
        self._model = model
        self._keep_alive = keep_alive
        self._system_prompt = system_prompt

    def set_keep_alive(self, keep_alive: str) -> None:
        """Live-update how long Ollama keeps the model warm (POST /settings/reload, no restart)."""
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
                "think": False,
                "keep_alive": self._keep_alive,
                "messages": [{"role": "user", "content": "ok"}],
            },
        )
        resp.raise_for_status()

    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        """Stream a conversation's reply. The system prompt is prepended.

        `think` is always explicit: left unset, a hybrid model reasons by default and takes tens of
        seconds to produce its first visible word.
        """
        payload: list[Message] = [{"role": "system", "content": self._system_prompt}]
        payload.extend(messages)
        async with self._client.stream(
            "POST",
            "/api/chat",
            json={
                "model": self._model,
                "stream": True,
                "think": reasoning,
                "keep_alive": self._keep_alive,
                "messages": payload,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                message = data.get("message", {})
                # A line can carry reasoning, answer text, or both.
                thought = message.get("thinking", "")
                if thought:
                    yield Chunk(thought, reasoning=True)
                token = message.get("content", "")
                if token:
                    yield Chunk(token)
                if data.get("done", False):
                    break

    async def close(self) -> None:
        await self._client.aclose()
