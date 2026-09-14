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


def test_current_config_exposes_keep_alive() -> None:
    view = current_config(_settings(llm_keep_alive="45m"))
    assert view.llm_keep_alive == "45m"


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


def test_apply_round_trips_prose(tmp_path: Path) -> None:
    """Prompts with newlines, # and quotes must survive being written to .env and read back."""
    env = tmp_path / ".env"
    prompt = 'Be terse.\nUse #1 as the example.\nSay "hello" first. '
    apply_config(_settings(), {"system_prompt": prompt}, env_path=env)
    assert Settings(_env_file=env, llm_model="m").system_prompt == prompt  # type: ignore[call-arg]


def test_apply_twice_leaves_no_residue(tmp_path: Path) -> None:
    # Multi-line values must be replaced cleanly, without leftover lines building up.
    env = tmp_path / ".env"
    apply_config(_settings(), {"system_prompt": "one\ntwo"}, env_path=env)
    apply_config(_settings(), {"system_prompt": "three\nfour"}, env_path=env)
    assert Settings(_env_file=env, llm_model="m").system_prompt == "three\nfour"  # type: ignore[call-arg]
    assert "one" not in env.read_text()


def test_an_environment_variable_outranks_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment variables beat .env, which is why the overlay must not export .env to the engine it spawns.

    See read_env_file in overlay/src-tauri/src/lib.rs.
    """
    env = tmp_path / ".env"
    apply_config(_settings(), {"audio_capture_app": "FromFile"}, env_path=env)
    monkeypatch.setenv("INTERVIEWR_AUDIO_CAPTURE_APP", "FromEnvVar")
    assert Settings(_env_file=env, llm_model="m").audio_capture_app == "FromEnvVar"  # type: ignore[call-arg]

    monkeypatch.delenv("INTERVIEWR_AUDIO_CAPTURE_APP")
    assert Settings(_env_file=env, llm_model="m").audio_capture_app == "FromFile"  # type: ignore[call-arg]


def test_apply_leaves_plain_values_unquoted(tmp_path: Path) -> None:
    # Quoting is for values that need it; .env stays readable for the ordinary ones.
    env = tmp_path / ".env"
    apply_config(_settings(), {"llm_model": "qwen2.5:7b"}, env_path=env)
    assert "INTERVIEWR_LLM_MODEL=qwen2.5:7b" in env.read_text()


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
