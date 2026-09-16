"""Whisper.cpp transcription via pywhispercpp."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from pathlib import Path
from typing import NamedTuple

import httpx
import numpy as np

_log = logging.getLogger(__name__)

# Time limits for the model download: generous for slow links, but never an indefinite hang.
_CONNECT_TIMEOUT_S = 15.0
_READ_TIMEOUT_S = 60.0


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
        self, model_name: str = "base.en", use_gpu: bool = True, gpu_device: int = -1
    ) -> None:
        from pywhispercpp.model import ContextParams, Model

        # whisper.cpp uses Vulkan device 0 by default, often the integrated GPU; gpu_device picks another.
        ctx: ContextParams = {"use_gpu": use_gpu}
        if gpu_device >= 0:
            ctx["gpu_device"] = gpu_device
        self._model = Model(
            model_name, print_realtime=False, print_progress=False, context_params=ctx
        )

    def warmup(self) -> None:
        """Runs one dummy transcription so the one-time Vulkan shader compile happens at startup."""
        self._model.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe_raw(self, audio: np.ndarray) -> str:
        """Transcribes a float32 buffer and cleans the text, keeping no state between calls."""
        segments = self._model.transcribe(
            audio,
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
) -> AsyncGenerator[Hypothesis, None]:
    """Streams a live transcript, committing words once two back-to-back transcriptions agree on them."""
    loop = asyncio.get_event_loop()
    speech: list[np.ndarray] = []
    speech_s = 0.0  # above-threshold audio; gates out tiny noise blips
    buffered_s = 0.0  # total audio since the segment opened
    trailing_silence_s = 0.0
    since_partial_s = 0.0  # time since the last partial transcription
    prev_hyp: list[str] = []  # previous hypothesis, for 2-pass agreement
    committed = 0  # words already emitted as final this utterance

    async def transcribe_buffer() -> list[str]:
        audio = np.concatenate(speech)
        drop = int((trailing_silence_s - TRIM_KEEP_S) * sample_rate)
        if 0 < drop < len(audio):
            audio = audio[:-drop]  # don't feed a long silent tail to whisper
        text = await loop.run_in_executor(None, transcriber.transcribe_raw, audio)
        return text.split()

    def reset() -> None:
        nonlocal speech, speech_s, buffered_s, trailing_silence_s, since_partial_s
        nonlocal prev_hyp, committed
        speech = []
        speech_s = 0.0
        buffered_s = 0.0
        trailing_silence_s = 0.0
        since_partial_s = 0.0
        prev_hyp = []
        committed = 0

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
                hyp = await transcribe_buffer()  # highest-quality pass; trust it fully
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
            hyp = await transcribe_buffer()
            agree = _common_prefix(prev_hyp, hyp)
            # Hold back the newest agreed words, since whisper often revises the most recent ones.
            safe = max(committed, agree - commit_margin_words)
            if safe > committed:
                yield Hypothesis(" ".join(hyp[committed:safe]), True)
                committed = safe
            if emit_partials:
                yield Hypothesis(" ".join(hyp[committed:]), False)
            prev_hyp = hyp
