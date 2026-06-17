"""Gemini streaming LLM client (BYOK via Google AI Studio free-tier keys).

SDK and model ids are config-switchable; the import is lazy so a local-only install never
needs `google-genai`. Conversation history lives in the Session; SYSTEM_PROMPT is injected
as Gemini's `system_instruction`, never as a history turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

from .base import SYSTEM_PROMPT, Message

if TYPE_CHECKING:
    from google.genai import Client


class GeminiClient:
    def __init__(self, api_key: str, model: str, max_output_tokens: int = 1024) -> None:
        from google import genai  # lazy: cloud extra, only when provider=gemini

        self._client: Client = genai.Client(api_key=api_key)
        self._model = model
        self._max_output_tokens = max_output_tokens

    async def check_health(self) -> bool:
        # Cheap metadata fetch: validates the key and model without spending generation tokens.
        from google.genai import errors

        try:
            await self._client.aio.models.get(model=self._model)
            return True
        except errors.APIError:
            return False

    async def warmup(self) -> None:
        # Cloud models have no cold-start to absorb.
        return None

    async def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Stream tokens. Gemini uses role 'model' for the assistant; system goes in config."""
        from google.genai import types

        contents: list[Any] = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=self._max_output_tokens,
        )
        stream = await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        )
        async for chunk in stream:
            token = chunk.text
            if token:
                yield token

    async def close(self) -> None:
        # Best-effort: the SDK holds an httpx client behind a private handle.
        aio = getattr(self._client, "aio", None)
        api_client = getattr(aio, "_api_client", None)
        httpx_client = getattr(api_client, "_async_httpx_client", None)
        if httpx_client is not None:
            await httpx_client.aclose()
