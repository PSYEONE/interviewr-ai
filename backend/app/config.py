"""Central configuration. Env vars (prefix INTERVIEWR_) or .env file."""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The backend/ folder and the repo root, located from this file.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_checkout() -> bool:
    """Whether we're running from a git checkout (has .git and is writable) rather than an installed copy."""
    return (_REPO_ROOT / ".git").is_dir() and os.access(_REPO_ROOT, os.W_OK)


def _xdg(var: str, fallback: str) -> Path:
    """The interviewr folder inside an XDG base directory; relative values are ignored."""
    value = os.environ.get(var, "")
    base = Path(value) if value.startswith("/") else Path.home() / fallback
    return base / "interviewr"


def config_dir() -> Path:
    """Directory holding `.env`: INTERVIEWR_CONFIG_DIR, else the checkout root, else ~/.config/interviewr."""
    if override := os.environ.get("INTERVIEWR_CONFIG_DIR"):
        return Path(override)
    return _REPO_ROOT if _is_checkout() else _xdg("XDG_CONFIG_HOME", ".config")


def state_dir() -> Path:
    """Directory holding the RAG and history databases."""
    if override := os.environ.get("INTERVIEWR_STATE_DIR"):
        return Path(override)
    return _BACKEND_DIR / "data" if _is_checkout() else _xdg("XDG_DATA_HOME", ".local/share")


# Resolved absolutely so it loads regardless of the process cwd.
_ENV_FILE = config_dir() / ".env"


class STTProvider(StrEnum):
    WHISPER_CPP = "whisper_cpp"
    GROQ = "groq"
    DEEPGRAM = "deepgram"


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"
    OLLAMA = "ollama"


class EmbedProvider(StrEnum):
    OLLAMA = "ollama"  # local default; no key, runs offline
    OPENAI = "openai"
    GEMINI = "gemini"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_prefix="INTERVIEWR_",
        extra="ignore",
    )

    # transport
    host: str = "127.0.0.1"
    port: int = 8765
    enable_remote_viewer: bool = False
    auth_token: str = Field(
        default="",
        description="WS auth token. Generate with `openssl rand -hex 32`.",
    )

    # audio
    audio_device: str = ""  # PipeWire source name; empty = auto-detect default sink monitor
    # App to capture on its own (empty = all system audio); takes precedence over audio_device.
    audio_capture_app: str = ""

    # stt
    stt_provider: STTProvider = STTProvider.WHISPER_CPP
    whisper_model: str = "small.en"  # best accuracy/speed tradeoff on test-clean (see eval/)
    whisper_use_gpu: bool = True  # Vulkan; falls back to CPU
    whisper_gpu_device: int = -1  # Vulkan device index; -1 = default (0). Pin the discrete GPU on iGPU+dGPU laptops
    vad_silence_rms: float = 0.01  # skip whisper on windows quieter than this (RMS)
    vad_silence_hold_s: float = 0.7  # trailing silence that ends an utterance
    vad_max_utterance_s: float = 15.0  # force a flush during long continuous speech; low values cut sentences and hurt accuracy
    stt_emit_partials: bool = True  # stream a live tentative tail (off = commit-only, cheaper on CPU)
    stt_partial_interval_s: float = 0.4  # how often to re-transcribe for the live tail
    stt_commit_margin_words: int = 3  # hold back this many live-edge words before committing

    # llm
    llm_provider: LLMProvider = LLMProvider.OLLAMA
    llm_model: str = ""  # set via INTERVIEWR_LLM_MODEL (e.g. qwen3:8b)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"  # OpenAI-compatible endpoint
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_models_dir: str = ""  # empty = auto-detect (~/.ollama, then the systemd install path)
    llm_keep_alive: str = "30m"  # how long Ollama keeps the model in VRAM after last use
    system_prompt: str = ""  # empty = built-in default (llm/base.py SYSTEM_PROMPT)
    # Per-answer-style directives; empty = built-in default (llm/base.py STYLE_DIRECTIVES).
    answer_style_script: str = ""
    answer_style_bullets: str = ""

    # rag (retrieval over resume + job description)
    rag_enabled: bool = True
    embed_provider: EmbedProvider = EmbedProvider.OLLAMA
    embed_model: str = "nomic-embed-text"  # ollama default; `ollama pull nomic-embed-text`
    rag_top_k: int = 4  # chunks injected per answer
    rag_min_score: float = 0.0  # drop matches below this cosine score (0 = keep top_k)
    rag_chunk_chars: int = 1000
    rag_chunk_overlap: int = 200
    rag_db_path: str = ""  # empty = rag.sqlite3 in state_dir()
    resume_path: str = ""  # optional: ingest this file at startup (headless/dev convenience)
    job_desc_path: str = ""  # optional: ingest this file at startup

    # server / session
    debug_print: bool = False  # also print transcripts to stdout (dev)
    max_buffer_chars: int = 2000  # rolling transcript buffer fed to an answer
    max_history_chars: int = 3000  # retained prior Q&A context
    event_queue_maxsize: int = 256  # per-subscriber bounded queue

    # conversation history (local-only persistence of answered Q&A turns)
    history_enabled: bool = True  # persist turns to local sqlite; off = nothing is written
    history_db_path: str = ""  # empty = history.sqlite3 in state_dir()

    @field_validator("stt_provider", "llm_provider", "embed_provider", mode="before")
    @classmethod
    def _blank_provider_to_default(cls, v: object, info: ValidationInfo) -> object:
        # A blank provider in .env means "use the default" rather than an invalid value.
        if v == "" and info.field_name is not None:
            return cls.model_fields[info.field_name].get_default()
        return v

    @model_validator(mode="after")
    def _guard_remote_exposure(self) -> "Settings":
        if self.enable_remote_viewer:
            self.host = "0.0.0.0"  # noqa: S104
            if len(self.auth_token) < 16:
                raise ValueError(
                    "INTERVIEWR_ENABLE_REMOTE_VIEWER=True requires "
                    "INTERVIEWR_AUTH_TOKEN with >=16 chars."
                )
        return self

    @model_validator(mode="after")
    def _validate_llm(self) -> "Settings":
        # No model yet means setup mode; once a model is set, cloud providers need their API key.
        if not self.llm_model:
            return self
        required_key = {
            LLMProvider.ANTHROPIC: ("INTERVIEWR_ANTHROPIC_API_KEY", self.anthropic_api_key),
            LLMProvider.OPENAI: ("INTERVIEWR_OPENAI_API_KEY", self.openai_api_key),
            LLMProvider.GEMINI: ("INTERVIEWR_GEMINI_API_KEY", self.gemini_api_key),
            LLMProvider.GROQ: ("INTERVIEWR_GROQ_API_KEY", self.groq_api_key),
        }.get(self.llm_provider)
        if required_key is not None and not required_key[1]:
            raise ValueError(f"INTERVIEWR_LLM_PROVIDER={self.llm_provider} requires {required_key[0]}.")
        return self

    @model_validator(mode="after")
    def _validate_embed(self) -> "Settings":
        # Cloud embedders need their key; the local default (Ollama) needs nothing.
        if not self.rag_enabled:
            return self
        required_key = {
            EmbedProvider.OPENAI: ("INTERVIEWR_OPENAI_API_KEY", self.openai_api_key),
            EmbedProvider.GEMINI: ("INTERVIEWR_GEMINI_API_KEY", self.gemini_api_key),
        }.get(self.embed_provider)
        if required_key is not None and not required_key[1]:
            raise ValueError(
                f"INTERVIEWR_EMBED_PROVIDER={self.embed_provider} requires {required_key[0]}."
            )
        return self

    def resolved_ollama_models_dir(self) -> Path | None:
        """Explicit override, or None meaning "probe the default locations"."""
        return Path(self.ollama_models_dir) if self.ollama_models_dir else None

    def resolved_rag_db_path(self) -> Path:
        return Path(self.rag_db_path) if self.rag_db_path else state_dir() / "rag.sqlite3"

    def resolved_history_db_path(self) -> Path:
        return Path(self.history_db_path) if self.history_db_path else state_dir() / "history.sqlite3"


@lru_cache
def get_settings() -> Settings:
    return Settings()
