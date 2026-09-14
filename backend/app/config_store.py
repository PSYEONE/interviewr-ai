"""Reads the current config (with secrets hidden) and validates changes before saving them to .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .config import _ENV_FILE, Settings
from .llm.base import effective_style_directives, effective_system_prompt

# Fields the wizard and settings screen may change (host, port and remote access are left out on purpose).
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
        "answer_style_script",
        "answer_style_bullets",
        "anthropic_api_key",
        "openai_api_key",
        "gemini_api_key",
        "groq_api_key",
        "embed_provider",
        "embed_model",
        "rag_enabled",
        "rag_top_k",
        "history_enabled",
        "audio_capture_app",
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
    audio_capture_app: str
    # Prompts are sent with the built-in defaults filled in, so the UI can show and edit them.
    system_prompt: str
    answer_style_script: str
    answer_style_bullets: str
    auth_token_set: bool
    anthropic_api_key_set: bool
    openai_api_key_set: bool
    gemini_api_key_set: bool
    groq_api_key_set: bool


def current_config(settings: Settings) -> ConfigView:
    styles = effective_style_directives(settings)
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
        audio_capture_app=settings.audio_capture_app,
        system_prompt=effective_system_prompt(settings),
        answer_style_script=styles["script"],
        answer_style_bullets=styles["bullets"],
        auth_token_set=bool(settings.auth_token),
        anthropic_api_key_set=bool(settings.anthropic_api_key),
        openai_api_key_set=bool(settings.openai_api_key),
        gemini_api_key_set=bool(settings.gemini_api_key),
        groq_api_key_set=bool(settings.groq_api_key),
    )


def apply_config(
    settings: Settings, updates: dict[str, object], *, env_path: Path | None = None
) -> None:
    """Validates `updates` against the full config and writes them to .env; raises ValueError if they're invalid."""
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
    text = str(value)
    if not _needs_quoting(text):
        return text
    # Quote values dotenv would otherwise cut off at a newline or "#"; these are the escapes it decodes.
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _needs_quoting(text: str) -> bool:
    """Quote only what would not survive being read back, so .env stays readable."""
    return (
        any(c in text for c in '\n\r"\\#')
        or text != text.strip()
        or text.startswith("'")
    )


def _write_env(updates: dict[str, object], path: Path) -> None:
    """Update or append `INTERVIEWR_<FIELD>` lines, leaving the rest of .env intact."""
    # The config folder may not exist yet the first time an installed copy saves.
    path.parent.mkdir(parents=True, exist_ok=True)
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
