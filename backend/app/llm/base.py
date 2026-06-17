"""Shared LLM types and the provider-agnostic client protocol.

The engine talks to one LLMClient. Providers are adapters behind this protocol so
overlay/transport code never learns which model is answering. SYSTEM_PROMPT and the
Message shape live here because every adapter prepends the former and consumes the latter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol, TypedDict, runtime_checkable

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


@runtime_checkable
class LLMClient(Protocol):
    """Stateless streaming LLM. Conversation history lives in the Session, not here.

    Adapters prepend SYSTEM_PROMPT themselves; `messages` carries only user/assistant turns.
    """

    async def check_health(self) -> bool: ...

    async def warmup(self) -> None: ...

    def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...
