"""Shared LLM types, the built-in prompts, and the provider-agnostic LLMClient protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from ..config import Settings

SYSTEM_PROMPT = (
    "You are a real-time meeting and interview copilot. "
    "You receive live audio transcription and provide concise, helpful responses. "
    "Focus on answering questions detected in the transcript, "
    "providing relevant context, and surfacing key points. "
    "Be direct and brief."
)

# Formatting instructions added to the question for each answer style; users can override them like the system prompt.
STYLE_DIRECTIVES: dict[str, str] = {
    "script": (
        "STYLE: Answer as a natural first-person spoken script, what the candidate would say "
        "out loud. Conversational and flowing. No bullet points, lists, or headers."
    ),
    "bullets": (
        "STYLE: Answer as 3 to 6 concise bullet points, each a single line to glance at and "
        "expand on. No long paragraphs."
    ),
}


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


def effective_system_prompt(settings: Settings) -> str:
    """The system prompt to use: the user's override if set, else the built-in default."""
    return settings.system_prompt or SYSTEM_PROMPT


def effective_style_directives(settings: Settings) -> dict[str, str]:
    """Per-style directives: each the user's override if set, else the built-in default."""
    return {
        "script": settings.answer_style_script or STYLE_DIRECTIVES["script"],
        "bullets": settings.answer_style_bullets or STYLE_DIRECTIVES["bullets"],
    }
