"""Decode arbitrary audio files to the float32 PCM the STT pipeline expects.

ffmpeg handles every container/codec and resampling in one subprocess, matching the
project's subprocess-audio approach (pw-record) and avoiding a libsndfile/scipy dep."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


def load_audio(path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Decode ``path`` to a mono float32 numpy array at ``sample_rate``.

    Returns samples in roughly [-1, 1], the same shape the capture pipeline yields.
    """
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()
