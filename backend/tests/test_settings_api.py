"""config_store: redaction in the view, and validate-before-write on apply."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.config_store import ConfigView, apply_config, current_config


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"llm_model": "llama3.1:8b", "_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_current_config_redacts_secrets() -> None:
    view = current_config(_settings(anthropic_api_key="sk-secret", auth_token="tok"))
    assert isinstance(view, ConfigView)
    assert view.anthropic_api_key_set is True
    assert view.openai_api_key_set is False
    assert view.auth_token_set is True
    # The secret value must not appear anywhere in the serialized view.
    assert "sk-secret" not in view.model_dump_json()


def test_apply_rejects_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-writable"):
        apply_config(_settings(), {"host": "0.0.0.0"}, env_path=tmp_path / ".env")


def test_apply_rejects_cloud_provider_without_key(tmp_path: Path) -> None:
    # Switching to OpenAI with no key must fail the Settings validator, before any write.
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="INTERVIEWR_OPENAI_API_KEY"):
        apply_config(_settings(), {"llm_provider": "openai"}, env_path=env)
    assert not env.exists()


def test_apply_writes_only_changed_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# header\nINTERVIEWR_LLM_MODEL=old\nINTERVIEWR_AUTH_TOKEN=keepme\n")
    apply_config(
        _settings(),
        {"llm_model": "qwen2.5:7b", "rag_top_k": 6},
        env_path=env,
    )
    text = env.read_text()
    assert "# header" in text
    assert "INTERVIEWR_AUTH_TOKEN=keepme" in text
    assert "INTERVIEWR_LLM_MODEL=qwen2.5:7b" in text
    assert "INTERVIEWR_LLM_MODEL=old" not in text
    assert "INTERVIEWR_RAG_TOP_K=6" in text


def test_apply_accepts_valid_cloud_combo(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    apply_config(
        _settings(),
        {"llm_provider": "anthropic", "anthropic_api_key": "sk-xyz", "llm_model": "claude-opus-4-8"},
        env_path=env,
    )
    text = env.read_text()
    assert "INTERVIEWR_LLM_PROVIDER=anthropic" in text
    assert "INTERVIEWR_ANTHROPIC_API_KEY=sk-xyz" in text
