"""Whisper.cpp transcription via pywhispercpp."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

import numpy as np


class WhisperTranscriber:
    """Wraps pywhispercpp Model for utterance transcription with dedup."""

    def __init__(self, model_name: str = "base.en", use_gpu: bool = True) -> None:
        from pywhispercpp.model import Model

        self._model = Model(model_name, print_realtime=False, print_progress=False)
        self._prev_words: list[str] = []

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 audio buffer, returning only new text."""
        segments = self._model.transcribe(audio)
        full_text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        curr_words = full_text.split()
        new_text = self._deduplicate(curr_words)
        self._prev_words = curr_words
        return new_text

    def reset_context(self) -> None:
        """Forget the previous window. A silence gap is a natural dedup boundary."""
        self._prev_words = []

    def _deduplicate(self, curr_words: list[str]) -> str:
        """Strip overlapping prefix that matches the tail of the previous output."""
        if not self._prev_words or not curr_words:
            return " ".join(curr_words)

        # find longest suffix of prev that matches a prefix of curr
        max_overlap = min(len(self._prev_words), len(curr_words))
        overlap = 0
        for length in range(1, max_overlap + 1):
            if self._prev_words[-length:] == curr_words[:length]:
                overlap = length

        return " ".join(curr_words[overlap:])


# Whisper emits non-speech annotations like [BLANK_AUDIO] or (wind blowing) on
# ambiguous audio. Strip bracketed/parenthesized groups; spoken text rarely uses them.
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def _clean(text: str) -> str:
    return " ".join(_ANNOTATION.sub(" ", text).split())


async def transcribe_stream(
    audio_stream: AsyncIterator[np.ndarray],
    transcriber: WhisperTranscriber,
    sample_rate: int = 16000,
    silence_rms: float = 0.01,
    silence_hold_s: float = 0.7,
    min_speech_s: float = 0.4,
    max_utterance_s: float = 18.0,
) -> AsyncIterator[str]:
    """Segment audio by silence and transcribe each complete utterance once.

    Chunks quieter than ``silence_rms`` are silence. Speech is accumulated until
    ``silence_hold_s`` of trailing silence ends the utterance (or ``max_utterance_s``
    forces a flush). Transcribing whole utterances with no window overlap avoids the
    re-transcription and duplicate text that sliding windows produce, and skipping
    silent stretches keeps idle CPU near zero.
    """
    loop = asyncio.get_event_loop()
    speech: list[np.ndarray] = []
    speech_s = 0.0  # above-threshold audio only; gates out tiny noise blips
    buffered_s = 0.0  # total audio held since the segment opened; the latency bound
    trailing_silence_s = 0.0

    async def flush() -> str | None:
        nonlocal speech, speech_s, buffered_s, trailing_silence_s
        had_speech = speech_s
        audio = np.concatenate(speech) if speech else None
        speech = []
        speech_s = 0.0
        buffered_s = 0.0
        trailing_silence_s = 0.0
        if audio is None or had_speech < min_speech_s:
            return None
        transcriber.reset_context()  # each utterance is independent
        text = await loop.run_in_executor(None, transcriber.transcribe, audio)
        return _clean(text) or None

    async for chunk in audio_stream:
        chunk_s = len(chunk) / sample_rate
        rms = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.0

        if rms >= silence_rms:
            speech.append(chunk)
            speech_s += chunk_s
            buffered_s += chunk_s
            trailing_silence_s = 0.0
        elif speech:
            speech.append(chunk)  # keep a little trailing silence as context
            buffered_s += chunk_s
            trailing_silence_s += chunk_s

        if speech and (trailing_silence_s >= silence_hold_s or buffered_s >= max_utterance_s):
            text = await flush()
            if text:
                yield text
