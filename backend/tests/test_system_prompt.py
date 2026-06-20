"""System-prompt config: default fallback + per-adapter wiring through build_llm."""

from __future__ import annotations

import pytest

from app.config import LLMProvider, Settings
from app.llm import build_llm
from app.llm.base import SYSTEM_PROMPT, effective_system_prompt


def test_effective_prompt_defaults_to_builtin() -> None:
    base = Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]
    assert effective_system_prompt(base) == SYSTEM_PROMPT
    override = Settings(llm_model="m", system_prompt="be terse", _env_file=None)  # type: ignore[call-arg]
    assert effective_system_prompt(override) == "be terse"


def test_build_llm_ollama_uses_effective_prompt() -> None:
    default = build_llm(
        Settings(llm_model="m", llm_provider=LLMProvider.OLLAMA, _env_file=None)  # type: ignore[call-arg]
    )
    assert default._system_prompt == SYSTEM_PROMPT  # type: ignore[attr-defined]
    custom = build_llm(
        Settings(  # type: ignore[call-arg]
            llm_model="m",
            llm_provider=LLMProvider.OLLAMA,
            system_prompt="pirate mode",
            _env_file=None,
        )
    )
    assert custom._system_prompt == "pirate mode"  # type: ignore[attr-defined]


def test_build_llm_cloud_passes_prompt() -> None:
    pytest.importorskip("anthropic")
    client = build_llm(
        Settings(  # type: ignore[call-arg]
            llm_model="claude-3-5-haiku-latest",
            llm_provider=LLMProvider.ANTHROPIC,
            anthropic_api_key="sk-test",
            system_prompt="cloud terse",
            _env_file=None,
        )
    )
    assert client._system_prompt == "cloud terse"  # type: ignore[attr-defined]
