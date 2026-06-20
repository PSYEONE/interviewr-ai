"""Read the current effective config (secrets redacted) and apply changes safely.

A change is validated by constructing the real ``Settings`` model with the merged values, so
a bad provider/key/model combo is rejected by the same validators that guard startup, before
anything is written. Only an allow-listed set of fields is writable, and writes touch just
those keys in ``.env`` (preserving the user's comments/layout). Secret values are never echoed
back; ``GET /settings`` exposes only whether each key is set.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .config import _ENV_FILE, Settings
from .llm.base import effective_system_prompt

# Fields the wizard / settings page may change. Deliberately excludes transport/security
# fields (host, port, enable_remote_viewer) which are not part of the setup flow.
_WRITABLE: frozenset[str] = frozenset(
    {
        "stt_provider",
        "whisper_model",
        "whisper_use_gpu",
        "whisper_gpu_device",
        "llm_provider",
        "llm_model",
        "ollama_base_url",
        "llm_keep_alive",
        "system_prompt",
        "anthropic_api_key",
        "openai_api_key",
        "gemini_api_key",
        "groq_api_key",
        "embed_provider",
        "embed_model",
        "rag_enabled",
        "rag_top_k",
        "history_enabled",
    }
)


class ConfigView(BaseModel):
    """Current config for the settings UI. Secrets are reported as set/unset booleans only."""

    stt_provider: str
    whisper_model: str
    whisper_gpu_device: int
    whisper_use_gpu: bool
    llm_provider: str
    llm_model: str
    ollama_base_url: str
    llm_keep_alive: str
    embed_provider: str
    embed_model: str
    rag_enabled: bool
    rag_top_k: int
    history_enabled: bool
    system_prompt: str  # resolved: the override if set, else the built-in default
    auth_token_set: bool
    anthropic_api_key_set: bool
    openai_api_key_set: bool
    gemini_api_key_set: bool
    groq_api_key_set: bool


def current_config(settings: Settings) -> ConfigView:
    return ConfigView(
        stt_provider=settings.stt_provider,
        whisper_model=settings.whisper_model,
        whisper_gpu_device=settings.whisper_gpu_device,
        whisper_use_gpu=settings.whisper_use_gpu,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        ollama_base_url=settings.ollama_base_url,
        llm_keep_alive=settings.llm_keep_alive,
        embed_provider=settings.embed_provider,
        embed_model=settings.embed_model,
        rag_enabled=settings.rag_enabled,
        rag_top_k=settings.rag_top_k,
        history_enabled=settings.history_enabled,
        system_prompt=effective_system_prompt(settings),
        auth_token_set=bool(settings.auth_token),
        anthropic_api_key_set=bool(settings.anthropic_api_key),
        openai_api_key_set=bool(settings.openai_api_key),
        gemini_api_key_set=bool(settings.gemini_api_key),
        groq_api_key_set=bool(settings.groq_api_key),
    )


def apply_config(
    settings: Settings, updates: dict[str, object], *, env_path: Path | None = None
) -> None:
    """Validate `updates` against the merged config and write them to .env. Raises ValueError
    on an unknown field or a combination the Settings validators reject."""
    unknown = sorted(set(updates) - _WRITABLE)
    if unknown:
        raise ValueError(f"unknown or non-writable settings: {unknown}")
    merged = settings.model_dump()
    merged.update(updates)
    merged["_env_file"] = None  # validate in isolation, not against the on-disk .env
    # Re-validate the whole prospective config; ValidationError -> a clear ValueError message.
    Settings(**merged)
    _write_env(updates, env_path or _ENV_FILE)


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_env(updates: dict[str, object], path: Path) -> None:
    """Update or append `INTERVIEWR_<FIELD>` lines, leaving the rest of .env intact."""
    pending = {f"INTERVIEWR_{key.upper()}": _fmt(val) for key, val in updates.items()}
    lines = path.read_text().splitlines() if path.exists() else []
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                out.append(f"{key}={pending.pop(key)}")
                continue
        out.append(line)
    out.extend(f"{key}={val}" for key, val in pending.items())
    path.write_text("\n".join(out) + "\n")
