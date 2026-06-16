"""Whisper.cpp transcription via pywhispercpp."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np


class WhisperTranscriber:
    """Wraps pywhispercpp Model for windowed transcription with dedup."""

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


async def transcribe_stream(
    audio_stream: AsyncIterator[np.ndarray],
    transcriber: WhisperTranscriber,
    window_s: float = 5.0,
    slide_s: float = 3.0,
    sample_rate: int = 16000,
    silence_rms: float = 0.01,
) -> AsyncIterator[str]:
    """Accumulate audio into overlapping windows and yield transcribed text.

    Windows quieter than ``silence_rms`` skip whisper entirely: this cuts idle CPU
    to near zero and avoids the hallucinated text whisper emits on near-silent input.
    """
    window_samples = int(window_s * sample_rate)
    slide_samples = int(slide_s * sample_rate)
    buffer = np.array([], dtype=np.float32)
    loop = asyncio.get_event_loop()

    async for chunk in audio_stream:
        buffer = np.concatenate([buffer, chunk])
        if len(buffer) >= window_samples:
            window = buffer[:window_samples].copy()
            buffer = buffer[slide_samples:]

            rms = float(np.sqrt(np.mean(window**2)))
            if rms < silence_rms:
                transcriber.reset_context()  # silence is a dedup boundary
                continue

            text = await loop.run_in_executor(None, transcriber.transcribe, window)
            if text.strip():
                yield text
