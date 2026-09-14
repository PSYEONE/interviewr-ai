"""Streaming client for OpenAI-compatible chat APIs (OpenAI and Groq); openai is imported lazily so local installs don't need it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, cast

from .base import SYSTEM_PROMPT, Message

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class OpenAICompatClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        max_tokens: int = 1024,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        from openai import AsyncOpenAI  # lazy: cloud extra, only for openai-compatible providers

        # base_url=None lets the SDK use the real OpenAI endpoint; Groq passes its own.
        self._client: AsyncOpenAI = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt

    async def check_health(self) -> bool:
        # Validates the key and model with a cheap metadata call, no generation tokens spent.
        from openai import APIError

        try:
            await self._client.models.retrieve(self._model)
            return True
        except APIError:
            return False

    async def warmup(self) -> None:
        # Cloud models have no cold-start to absorb.
        return None

    async def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Stream tokens. The system prompt is prepended; messages are already OpenAI-shaped."""
        payload: list[Message] = [{"role": "system", "content": self._system_prompt}, *messages]
        # Message already matches OpenAI's format; the cast satisfies the SDK's stricter per-role types.
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=cast(Any, payload),
            stream=True,
            max_tokens=self._max_tokens,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            token = chunk.choices[0].delta.content
            if token:
                yield token

    async def close(self) -> None:
        await self._client.close()
