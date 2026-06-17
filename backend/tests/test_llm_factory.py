"""Factory routing, config validation, and protocol conformance for the LLM layer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from app.config import LLMProvider, Settings
from app.llm import build_llm
from app.llm.base import LLMClient, Message
from app.llm.ollama import OllamaClient


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"llm_model": "test-model", "_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class FakeLLM:
    async def check_health(self) -> bool:
        return True

    async def warmup(self) -> None:
        return None

    async def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield "ok"

    async def close(self) -> None:
        return None


def test_fake_and_ollama_satisfy_protocol() -> None:
    assert isinstance(FakeLLM(), LLMClient)
    assert isinstance(OllamaClient("http://x", "m"), LLMClient)


def test_build_llm_ollama() -> None:
    llm = build_llm(_settings(llm_provider=LLMProvider.OLLAMA))
    assert isinstance(llm, OllamaClient)


def test_build_llm_gemini() -> None:
    pytest.importorskip("google.genai")
    from app.llm.gemini import GeminiClient

    llm = build_llm(_settings(llm_provider=LLMProvider.GEMINI, gemini_api_key="k"))
    assert isinstance(llm, GeminiClient)


def test_build_llm_groq() -> None:
    pytest.importorskip("openai")
    from app.llm.openai_compat import OpenAICompatClient

    llm = build_llm(_settings(llm_provider=LLMProvider.GROQ, groq_api_key="k"))
    assert isinstance(llm, OpenAICompatClient)


@pytest.mark.parametrize("provider", [LLMProvider.ANTHROPIC, LLMProvider.OPENAI])
def test_build_llm_unimplemented(provider: LLMProvider) -> None:
    with pytest.raises(NotImplementedError):
        build_llm(_settings(llm_provider=provider))


def test_missing_model_rejected() -> None:
    with pytest.raises(ValueError, match="INTERVIEWR_LLM_MODEL"):
        _settings(llm_model="")


@pytest.mark.parametrize(
    ("provider", "key_var"),
    [
        (LLMProvider.GEMINI, "INTERVIEWR_GEMINI_API_KEY"),
        (LLMProvider.GROQ, "INTERVIEWR_GROQ_API_KEY"),
    ],
)
def test_missing_cloud_key_rejected(provider: LLMProvider, key_var: str) -> None:
    with pytest.raises(ValueError, match=key_var):
        _settings(llm_provider=provider, gemini_api_key="", groq_api_key="")
