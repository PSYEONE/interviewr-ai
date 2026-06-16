"""PipeWire audio capture via pw-record subprocess."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import numpy as np

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4800  # 0.3s at 16kHz
CHUNK_BYTES = CHUNK_SAMPLES * 4  # f32 = 4 bytes per sample

TargetKind = Literal["monitor", "mic", "app"]

# Pull the human-meaningful segment out of an ALSA HiFi node name,
# e.g. ...HiFi__Speaker__sink.monitor -> "Speaker", ...HiFi__Mic1__source -> "Mic1".
_HIFI_RE = re.compile(r"HiFi__(.+?)__(?:sink|source)")


@dataclass(frozen=True)
class CaptureTarget:
    id: str  # the source name passed to pw-record --target
    label: str  # human-friendly, for a selection UI
    kind: TargetKind


def _label_for(name: str, kind: TargetKind) -> str:
    if name.startswith("bluez"):
        base = "Bluetooth"
    else:
        match = _HIFI_RE.search(name)
        base = match.group(1) if match else name
    suffix = {"monitor": "system audio", "mic": "mic", "app": "app"}[kind]
    return f"{base} ({suffix})"


async def list_capture_targets() -> list[CaptureTarget]:
    """List available PipeWire capture sources, monitors first.

    Monitors are system-audio output taps (what you want to capture a call or video).
    Mics are real input devices. Per-application stream capture (sink-inputs) is not
    enumerated yet; it needs node-level targeting. See TODO.md.
    """
    proc = await asyncio.create_subprocess_exec(
        "pactl", "list", "short", "sources",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pactl list sources failed: {stderr.decode().strip()}")

    targets: list[CaptureTarget] = []
    for line in stdout.decode().splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        name = fields[1]
        kind: TargetKind = "monitor" if name.endswith(".monitor") else "mic"
        targets.append(CaptureTarget(id=name, label=_label_for(name, kind), kind=kind))

    # Monitors first (the common case for this app), otherwise stable order.
    targets.sort(key=lambda t: t.kind != "monitor")
    return targets


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
    """Yield float32 numpy arrays of audio from a PipeWire source.

    For a sink monitor (``<sink>.monitor``) we must target the sink node and set
    ``stream.capture.sink=true``. Passing the ``.monitor`` name to ``--target``
    makes pw-record silently fall back to the default source (the mic) whenever
    the sink is idle, which captures the user instead of the system audio.
    """
    chunk_bytes = chunk_samples * 4
    args = ["pw-record", "--rate", str(rate), "--channels", "1", "--format", "f32"]
    if device.endswith(".monitor"):
        args += ["-P", "stream.capture.sink=true", "--target", device[: -len(".monitor")]]
    else:
        args += ["--target", device]
    args.append("-")
    proc = await asyncio.create_subprocess_exec(
        *args,
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
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # pw-record already exited (e.g. on shutdown)
        await proc.wait()
