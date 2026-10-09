"""Anthropic (Claude) streaming client; the system prompt goes in the API's separate `system` parameter, not the messages."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, cast

from .base import SYSTEM_PROMPT, Chunk, Message

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


class AnthropicClient:
    def __init__(
        self, api_key: str, model: str, max_tokens: int = 1024, system_prompt: str = SYSTEM_PROMPT
    ) -> None:
        from anthropic import AsyncAnthropic  # lazy: cloud extra, only when provider=anthropic

        self._client: AsyncAnthropic = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt

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

    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        """Stream tokens. The system prompt goes in `system`; `messages` carries only turns.

        `reasoning` is accepted for the protocol and ignored: this provider's own reasoning API
        is not wired up.
        """
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system_prompt,
            messages=cast(Any, list(messages)),
        ) as stream:
            async for token in stream.text_stream:
                yield Chunk(token)

    async def close(self) -> None:
        await self._client.close()
