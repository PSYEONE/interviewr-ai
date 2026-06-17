"""Anthropic (Claude) streaming LLM client (BYOK).

SDK and model id are config-switchable; the import is lazy so a local-only install never
needs `anthropic`. Conversation history lives in the Session; SYSTEM_PROMPT is passed as
Anthropic's top-level `system` parameter, never as a history turn (the Messages API only
accepts user/assistant roles in `messages`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, cast

from .base import SYSTEM_PROMPT, Message

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


class AnthropicClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1024) -> None:
        from anthropic import AsyncAnthropic  # lazy: cloud extra, only when provider=anthropic

        self._client: AsyncAnthropic = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def check_health(self) -> bool:
        # Cheap metadata fetch: validates the key and model without spending generation tokens.
        from anthropic import APIError

        try:
            await self._client.models.retrieve(self._model)
            return True
        except APIError:
            return False

    async def warmup(self) -> None:
        # Cloud models have no cold-start to absorb.
        return None

    async def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Stream tokens. SYSTEM_PROMPT goes in `system`; `messages` carries only turns."""
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=cast(Any, list(messages)),
        ) as stream:
            async for token in stream.text_stream:
                yield token

    async def close(self) -> None:
        await self._client.close()
