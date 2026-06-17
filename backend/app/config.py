"""Central configuration. Env vars (prefix INTERVIEWR_) or .env file."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root is three levels up from this file (backend/app/config.py).
# Resolve .env absolutely so it loads regardless of the process cwd.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


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

    # stt
    stt_provider: STTProvider = STTProvider.WHISPER_CPP
    whisper_model: str = "small.en"  # best accuracy/speed tradeoff on test-clean (see eval/)
    whisper_use_gpu: bool = True  # Vulkan; falls back to CPU
    whisper_gpu_device: int = -1  # Vulkan device index; -1 = default (0). Pin the discrete GPU on iGPU+dGPU laptops
    vad_silence_rms: float = 0.01  # skip whisper on windows quieter than this (RMS)
    vad_silence_hold_s: float = 0.7  # trailing silence that ends an utterance
    vad_max_utterance_s: float = 15.0  # force a flush in continuous speech; bounds buffer/cost,
    # not latency (incremental commits handle that). Low values chop sentences and hurt accuracy.
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
    llm_keep_alive: str = "30m"  # how long Ollama keeps the model in VRAM after last use

    # server / session
    debug_print: bool = False  # also print transcripts to stdout (dev)
    max_buffer_chars: int = 2000  # rolling transcript buffer fed to an answer
    max_history_chars: int = 3000  # retained prior Q&A context
    event_queue_maxsize: int = 256  # per-subscriber bounded queue

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
        # Every provider needs a model id. BYOK cloud providers also need their key.
        # Fail at config time with a named var, not deep in the request path.
        if not self.llm_model:
            raise ValueError("INTERVIEWR_LLM_MODEL is required.")
        required_key = {
            LLMProvider.GEMINI: ("INTERVIEWR_GEMINI_API_KEY", self.gemini_api_key),
            LLMProvider.GROQ: ("INTERVIEWR_GROQ_API_KEY", self.groq_api_key),
        }.get(self.llm_provider)
        if required_key is not None and not required_key[1]:
            raise ValueError(f"INTERVIEWR_LLM_PROVIDER={self.llm_provider} requires {required_key[0]}.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
