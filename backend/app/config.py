"""Central configuration. Env vars (prefix INTERVIEWR_) or .env file."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class STTProvider(StrEnum):
    WHISPER_CPP = "whisper_cpp"
    GROQ = "groq"
    DEEPGRAM = "deepgram"


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # stt
    stt_provider: STTProvider = STTProvider.WHISPER_CPP
    whisper_model: str = "base.en"
    whisper_use_gpu: bool = True  # Vulkan; falls back to CPU

    # llm
    llm_provider: LLMProvider = LLMProvider.OLLAMA
    llm_model: str = ""  # set via INTERVIEWR_LLM_MODEL (e.g. qwen3:8b)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
