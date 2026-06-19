"""LLM provider factory. Maps settings.llm_provider to a concrete LLMClient.

Adapter modules are imported per-branch so an unused provider's SDK is never required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import LLMProvider
from .base import LLMClient

if TYPE_CHECKING:
    from ..config import Settings


def build_llm(settings: Settings) -> LLMClient:
    match settings.llm_provider:
        case LLMProvider.OLLAMA:
            from .ollama import OllamaClient

            return OllamaClient(
                settings.ollama_base_url, settings.llm_model, settings.llm_keep_alive
            )
        case LLMProvider.GEMINI:
            from .gemini import GeminiClient

            return GeminiClient(settings.gemini_api_key, settings.llm_model)
        case LLMProvider.GROQ:
            from .openai_compat import OpenAICompatClient

            return OpenAICompatClient(
                settings.groq_api_key, settings.llm_model, base_url=settings.groq_base_url
            )
        case LLMProvider.OPENAI:
            from .openai_compat import OpenAICompatClient

            # Same client as Groq; base_url=None lets the SDK hit the real OpenAI endpoint.
            return OpenAICompatClient(settings.openai_api_key, settings.llm_model)
        case LLMProvider.ANTHROPIC:
            from .anthropic import AnthropicClient

            return AnthropicClient(settings.anthropic_api_key, settings.llm_model)


__all__ = ["LLMClient", "build_llm"]
