"""Whisper.cpp transcription via pywhispercpp."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import httpx
import numpy as np

if TYPE_CHECKING:
    from pywhispercpp.model import Model

_log = logging.getLogger(__name__)

# Time limits for the model download: generous for slow links, but never an indefinite hang.
_CONNECT_TIMEOUT_S = 15.0
_READ_TIMEOUT_S = 60.0


# Loading needs the weights plus working buffers; whisper.cpp segfaults instead of failing cleanly.
_VRAM_MARGIN = 1.2
_VRAM_EXTRA_MB = 256


def fits_in_vram(needed_mb: int, free_mb: int | None) -> bool:
    """Whether a model is safe to load on the GPU. Unmeasurable free memory is treated as fine."""
    return free_mb is None or free_mb >= needed_mb * _VRAM_MARGIN + _VRAM_EXTRA_MB


def available_languages() -> list[str]:
    """whisper.cpp's language codes, or [] when the local STT extra is not installed."""
    try:
        from pywhispercpp.model import Model
    except ImportError:
        return []
    return list(Model.available_languages())


def model_path(name: str) -> Path:
    """Where pywhispercpp looks for this model, so a download here is one it will use."""
    from pywhispercpp.constants import MODELS_DIR

    return Path(MODELS_DIR) / f"ggml-{name}.bin"


def ensure_model(name: str, on_progress: Callable[[int], None] | None = None) -> Path:
    """Downloads the ggml model unless it is already on disk. Blocking; call it in a thread."""
    target = model_path(name)
    if target.is_file():
        return target
    from pywhispercpp.constants import MODELS_BASE_URL, MODELS_PREFIX_URL

    # MODELS_PREFIX_URL carries the repo path and the file prefix, e.g. "resolve/main/ggml".
    url = f"{MODELS_BASE_URL}/{MODELS_PREFIX_URL}-{name}.bin"
    part = target.with_suffix(".bin.part")
    target.parent.mkdir(parents=True, exist_ok=True)
    _log.info("downloading speech model %s", name)
    try:
        timeout = httpx.Timeout(_READ_TIMEOUT_S, connect=_CONNECT_TIMEOUT_S)
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            done = 0
            step = -1
            with part.open("wb") as out:
                for chunk in response.iter_bytes(1024 * 256):
                    out.write(chunk)
                    done += len(chunk)
                    percent = int(done * 100 / total) if total else 0
                    if percent // 10 > step // 10:
                        step = percent
                        _log.info("speech model %s: %d%%", name, percent)
                    if on_progress is not None:
                        on_progress(percent)
        part.replace(target)
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"could not download speech model '{name}': {exc}") from exc
    _log.info("speech model %s ready", name)
    return target


class WhisperTranscriber:
    """Wraps pywhispercpp Model for whole-buffer transcription."""

    def __init__(
        self,
        model_name: str = "base.en",
        use_gpu: bool = True,
        gpu_device: int = -1,
        language: str = "auto",
    ) -> None:
        from pywhispercpp.model import ContextParams, Model

        # whisper.cpp uses Vulkan device 0 by default, often the integrated GPU; gpu_device picks another.
        ctx: ContextParams = {"use_gpu": use_gpu}
        if gpu_device >= 0:
            ctx["gpu_device"] = gpu_device
        self.on_gpu = use_gpu
        self.language = language
        # .en models have no language tokens; whisper.cpp ignores the setting for them, as do we.
        self.english_only = model_name.endswith(".en")
        # Transcription runs in an executor thread, so the model is guarded against release under it.
        self._lock = threading.Lock()
        self._model: Model | None = Model(
            model_name, print_realtime=False, print_progress=False, context_params=ctx
        )

    def _loaded(self) -> Model:
        if self._model is None:
            raise RuntimeError("the speech model has been released")
        return self._model

    def release(self) -> None:
        """Frees the GPU buffers by dropping the model; pywhispercpp calls whisper_free on the last ref."""
        with self._lock:
            self._model = None

    def warmup(self) -> None:
        """Runs one dummy transcription so the one-time Vulkan shader compile happens at startup."""
        with self._lock:
            self._loaded().transcribe(np.zeros(16000, dtype=np.float32))

    def set_language(self, language: str) -> None:
        """Changes the configured language; the stream applies it from its next utterance."""
        self.language = language

    def detects_language(self) -> bool:
        """Whether an utterance's language has to be found from its audio."""
        return not self.english_only and self.language == "auto"

    def resolve_language(self, audio: np.ndarray) -> str:
        """The code one utterance is decoded in, detected from the audio when configured to auto.

        Detection costs an encoder pass, the same one whisper.cpp spends internally on every
        "auto" call, so the stream runs it once per utterance and pins the result.
        """
        if self.english_only:
            return "en"
        if self.language != "auto":
            return self.language
        with self._lock:
            (code, prob), _all = self._loaded().auto_detect_language(audio)
        _log.info("detected language: %s (%.2f)", code, float(prob))
        return code

    def transcribe_raw(self, audio: np.ndarray, language: str | None = None) -> str:
        """Transcribes a float32 buffer and cleans the text, keeping no state between calls."""
        if self.english_only:
            language = "en"
        with self._lock:
            segments = self._loaded().transcribe(
                audio,
                # pywhispercpp only sets what it is given, and whisper.cpp's own default is "en",
                # which makes a multilingual model translate. So both are always passed.
                language=language or self.language,
                translate=False,
                temperature=0.0,  # greedy; no random sampling
                no_context=True,  # don't carry decoder state across calls (reduces drift)
                suppress_blank=True,
                suppress_nst=True,  # suppress non-speech tokens (music/sfx hallucinations)
            )
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        return _clean(text)


class Hypothesis(NamedTuple):
    """One stream result: final text is permanent, otherwise it replaces the previous tentative text."""

    text: str
    final: bool


# Strips non-speech notes whisper adds on unclear audio, like [BLANK_AUDIO] or (wind blowing).
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)")

# Minimum audio before a partial transcription; whisper makes up words on very short buffers.
PARTIAL_MIN_S = 1.0

# Silence kept at the end of a buffer; whisper makes up text ("thank you") for long silent tails.
TRIM_KEEP_S = 0.2

# Audio needed before the language is detected. On 1 s and 2 s of Bulgarian, large-v3-turbo said
# Greek; on the full clip it was right. Until then partials are tentative and nothing is committed.
DETECT_MIN_S = 3.0


def _collapse_repeats(words: list[str], limit: int = 2) -> list[str]:
    """Drops runs of the same word longer than ``limit``, a common whisper hallucination."""
    out: list[str] = []
    prev = ""
    run = 0
    for word in words:
        key = word.lower()
        run = run + 1 if key == prev else 1
        prev = key
        if run <= limit:
            out.append(word)
    return out


def _clean(text: str) -> str:
    return " ".join(_collapse_repeats(_ANNOTATION.sub(" ", text).split()))


def _common_prefix(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


async def transcribe_stream(
    audio_stream: AsyncIterator[np.ndarray],
    transcriber: WhisperTranscriber,
    sample_rate: int = 16000,
    silence_rms: float = 0.01,
    silence_hold_s: float = 0.7,
    min_speech_s: float = 0.4,
    max_utterance_s: float = 6.0,
    partial_interval_s: float = 0.4,
    commit_margin_words: int = 2,
    emit_partials: bool = True,
    language: Callable[[], str] | None = None,
) -> AsyncGenerator[Hypothesis, None]:
    """Streams a live transcript, committing words once two back-to-back transcriptions agree on them.

    ``language`` reads the configured language code at each utterance start, so a settings reload
    reaches a running stream without restarting it.
    """
    loop = asyncio.get_running_loop()
    speech: list[np.ndarray] = []
    speech_s = 0.0  # above-threshold audio; gates out tiny noise blips
    buffered_s = 0.0  # total audio since the segment opened
    trailing_silence_s = 0.0
    since_partial_s = 0.0  # time since the last partial transcription
    prev_hyp: list[str] = []  # previous hypothesis, for 2-pass agreement
    committed = 0  # words already emitted as final this utterance
    # Pinned for the whole utterance: detecting on every partial would flip languages mid-sentence.
    utterance_lang: str | None = None

    async def transcribe_buffer(final: bool) -> list[str]:
        nonlocal utterance_lang
        audio = np.concatenate(speech)
        drop = int((trailing_silence_s - TRIM_KEEP_S) * sample_rate)
        if 0 < drop < len(audio):
            audio = audio[:-drop]  # don't feed a long silent tail to whisper
        if utterance_lang is None:
            if language is not None:
                transcriber.set_language(language())
            enough = final or len(audio) >= DETECT_MIN_S * sample_rate
            if enough or not transcriber.detects_language():
                utterance_lang = await loop.run_in_executor(
                    None, transcriber.resolve_language, audio
                )
        # Before the pin, whisper.cpp detects for itself on each partial; that text stays tentative.
        text = await loop.run_in_executor(None, transcriber.transcribe_raw, audio, utterance_lang)
        return text.split()

    def reset() -> None:
        nonlocal speech, speech_s, buffered_s, trailing_silence_s, since_partial_s
        nonlocal prev_hyp, committed, utterance_lang
        speech = []
        speech_s = 0.0
        buffered_s = 0.0
        trailing_silence_s = 0.0
        since_partial_s = 0.0
        prev_hyp = []
        committed = 0
        utterance_lang = None

    async for chunk in audio_stream:
        chunk_s = len(chunk) / sample_rate
        rms = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.0

        if rms >= silence_rms:
            speech.append(chunk)
            speech_s += chunk_s
            buffered_s += chunk_s
            trailing_silence_s = 0.0
        elif speech:
            speech.append(chunk)  # keep trailing/intra silence as context
            buffered_s += chunk_s
            trailing_silence_s += chunk_s
        else:
            continue  # silence before any speech: nothing buffered yet

        since_partial_s += chunk_s
        ended = trailing_silence_s >= silence_hold_s or buffered_s >= max_utterance_s

        if ended:
            if speech_s >= min_speech_s:
                hyp = await transcribe_buffer(True)  # highest-quality pass; trust it fully
                if len(hyp) > committed:
                    yield Hypothesis(" ".join(hyp[committed:]), True)
                if emit_partials:
                    yield Hypothesis("", False)  # clear the tentative tail
            reset()
            continue

        if (
            speech_s >= min_speech_s
            and buffered_s >= PARTIAL_MIN_S
            and since_partial_s >= partial_interval_s
        ):
            since_partial_s = 0.0
            hyp = await transcribe_buffer(False)
            agree = _common_prefix(prev_hyp, hyp)
            # Hold back the newest agreed words, since whisper often revises the most recent ones.
            # Nothing is committed until the language is pinned, or a wrong early guess would stick.
            safe = max(committed, agree - commit_margin_words) if utterance_lang else committed
            if safe > committed:
                yield Hypothesis(" ".join(hyp[committed:safe]), True)
                committed = safe
            if emit_partials:
                yield Hypothesis(" ".join(hyp[committed:]), False)
            prev_hyp = hyp
