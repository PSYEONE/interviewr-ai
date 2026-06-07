"""PipeWire audio capture via pw-record subprocess."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4800  # 0.3s at 16kHz
CHUNK_BYTES = CHUNK_SAMPLES * 4  # f32 = 4 bytes per sample


async def get_default_monitor() -> str:
    """Auto-detect the default PipeWire sink's monitor source."""
    proc = await asyncio.create_subprocess_exec(
        "pactl", "info",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    for line in stdout.decode().splitlines():
        if line.strip().startswith("Default Sink:"):
            return line.split(":", 1)[1].strip() + ".monitor"
    raise RuntimeError("Could not determine default PipeWire sink from pactl info")


async def capture_audio(
    device: str,
    rate: int = SAMPLE_RATE,
    chunk_samples: int = CHUNK_SAMPLES,
) -> AsyncIterator[np.ndarray]:
    """Yield float32 numpy arrays of audio from a PipeWire monitor source."""
    chunk_bytes = chunk_samples * 4
    proc = await asyncio.create_subprocess_exec(
        "pw-record",
        "--rate", str(rate),
        "--channels", "1",
        "--format", "f32",
        "--target", device,
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        while True:
            data = await proc.stdout.readexactly(chunk_bytes)
            yield np.frombuffer(data, dtype=np.float32).copy()
    except asyncio.IncompleteReadError:
        pass
    finally:
        proc.terminate()
        await proc.wait()
