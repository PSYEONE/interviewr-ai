"""Whisper.cpp transcription via pywhispercpp."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import NamedTuple

import numpy as np


class WhisperTranscriber:
    """Wraps pywhispercpp Model for whole-buffer transcription."""

    def __init__(
        self, model_name: str = "base.en", use_gpu: bool = True, gpu_device: int = -1
    ) -> None:
        from pywhispercpp.model import ContextParams, Model

        # whisper.cpp defaults to Vulkan device 0, which on multi-GPU laptops is the
        # weak integrated GPU. gpu_device >= 0 pins the discrete GPU instead.
        ctx: ContextParams = {"use_gpu": use_gpu}
        if gpu_device >= 0:
            ctx["gpu_device"] = gpu_device
        self._model = Model(
            model_name, print_realtime=False, print_progress=False, context_params=ctx
        )

    def warmup(self) -> None:
        """Run one dummy inference up front. The first Vulkan transcribe pays a
        one-time shader compile (~20s); do it at startup, not on the first utterance."""
        self._model.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe_raw(self, audio: np.ndarray) -> str:
        """Full transcription of a float32 buffer, cleaned. No dedup state: streaming
        callers re-transcribe a growing buffer and compare whole hypotheses themselves."""
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
    """One emission from the stream: ``final`` text is settled and permanent;
    otherwise it is the tentative tail that replaces the previous tentative tail."""

    text: str
    final: bool


# Whisper emits non-speech annotations like [BLANK_AUDIO] or (wind blowing) on
# ambiguous audio. Strip bracketed/parenthesized groups; spoken text rarely uses them.
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)")

# Don't run a partial transcription on less audio than this; whisper hallucinates
# words on sub-second buffers.
PARTIAL_MIN_S = 1.0

# Keep at most this much trailing silence when transcribing; whisper invents text
# ("thank you", "I'd like to say") when it decodes a long silent tail.
TRIM_KEEP_S = 0.2


def _collapse_repeats(words: list[str], limit: int = 2) -> list[str]:
    """Drop runs of the same word longer than ``limit``. Looping repetition is a
    classic whisper hallucination on ambiguous audio; real speech rarely repeats 3+."""
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
) -> AsyncIterator[Hypothesis]:
    """Stream a live transcript using LocalAgreement-2.

    Audio is segmented by silence as before, but instead of transcribing each
    utterance once at the end, the growing buffer is re-transcribed every
    ``partial_interval_s``. A word is committed (``final=True``) only once two
    consecutive transcriptions agree on it; the unconfirmed tail is emitted as a
    tentative hypothesis (``final=False``) the client renders dim and replaces in
    place. On silence (``silence_hold_s``) or the ``max_utterance_s`` cap the buffer
    gets one final transcription, the remainder is committed, the tail cleared, and
    utterance state reset.
    """
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
            # Hold back the last few agreed words: the live edge lacks right-context and
            # whisper revises it, so two near-adjacent passes can agree on a wrong guess.
            safe = max(committed, agree - commit_margin_words)
            if safe > committed:
                yield Hypothesis(" ".join(hyp[committed:safe]), True)
                committed = safe
            if emit_partials:
                yield Hypothesis(" ".join(hyp[committed:]), False)
            prev_hyp = hyp
