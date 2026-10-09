"""Factory routing, config validation, and protocol conformance for the LLM layer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from app.config import EmbedProvider, LLMProvider, Settings, STTProvider
from app.llm import build_llm
from app.llm.base import Chunk, LLMClient, Message
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

    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        yield Chunk("ok")

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


def test_build_llm_openai() -> None:
    pytest.importorskip("openai")
    from app.llm.openai_compat import OpenAICompatClient

    llm = build_llm(_settings(llm_provider=LLMProvider.OPENAI, openai_api_key="k"))
    assert isinstance(llm, OpenAICompatClient)


def test_build_llm_anthropic() -> None:
    pytest.importorskip("anthropic")
    from app.llm.anthropic import AnthropicClient

    llm = build_llm(_settings(llm_provider=LLMProvider.ANTHROPIC, anthropic_api_key="k"))
    assert isinstance(llm, AnthropicClient)


def test_empty_model_allowed_for_setup_mode() -> None:
    # An empty model is the unconfigured state; the engine boots in setup mode (the wizard
    # fills it in) instead of refusing to start.
    assert _settings(llm_model="").llm_model == ""


def test_blank_provider_falls_back_to_default() -> None:
    # A blank `INTERVIEWR_LLM_PROVIDER=` line must not crash config load; it means "unset".
    s = _settings(llm_provider="", stt_provider="", embed_provider="")
    assert s.llm_provider is LLMProvider.OLLAMA
    assert s.stt_provider is STTProvider.WHISPER_CPP
    assert s.embed_provider is EmbedProvider.OLLAMA


@pytest.mark.parametrize(
    ("provider", "key_var"),
    [
        (LLMProvider.ANTHROPIC, "INTERVIEWR_ANTHROPIC_API_KEY"),
        (LLMProvider.OPENAI, "INTERVIEWR_OPENAI_API_KEY"),
        (LLMProvider.GEMINI, "INTERVIEWR_GEMINI_API_KEY"),
        (LLMProvider.GROQ, "INTERVIEWR_GROQ_API_KEY"),
    ],
)
def test_missing_cloud_key_rejected(provider: LLMProvider, key_var: str) -> None:
    with pytest.raises(ValueError, match=key_var):
        _settings(
            llm_provider=provider,
            anthropic_api_key="",
            openai_api_key="",
            gemini_api_key="",
            groq_api_key="",
        )
