"""PipeWire audio capture via pw-record subprocess."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import re
import signal
import sys
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal

import numpy as np

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4800  # 0.3s at 16kHz
CHUNK_BYTES = CHUNK_SAMPLES * 4  # f32 = 4 bytes per sample

_PR_SET_PDEATHSIG = 1  # linux/prctl.h

_log = logging.getLogger(__name__)


def _die_with_parent() -> None:
    """Runs in the pw-record child process: asks the kernel to stop it if the engine dies (Linux only)."""
    if sys.platform != "linux":
        return
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:  # noqa: BLE001 - hardening only; never block capture if prctl is unavailable
        pass

TargetKind = Literal["monitor", "mic", "app"]

# Picks the readable part of an ALSA HiFi node name, e.g. ...HiFi__Speaker__sink.monitor -> "Speaker".
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
    """Lists PipeWire capture sources: system audio monitors first, then microphones."""
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


async def get_default_sink() -> str:
    """The default PipeWire sink's name (no `.monitor` suffix)."""
    proc = await asyncio.create_subprocess_exec(
        "pactl", "info",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    for line in stdout.decode().splitlines():
        if line.strip().startswith("Default Sink:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("Could not determine default PipeWire sink from pactl info")


async def get_default_monitor() -> str:
    """Auto-detect the default PipeWire sink's monitor source."""
    return await get_default_sink() + ".monitor"


# Cached result of raw_output_args() for the installed pw-record.
_raw_args: list[str] | None = None


def raw_output_args(help_text: str) -> list[str]:
    """Flags that make pw-record write bare samples: PipeWire 1.2+ adds a file header unless told `--container raw`."""
    return ["--container", "raw"] if "--container" in help_text else []


async def _pw_record_raw_args() -> list[str]:
    """raw_output_args() for the installed pw-record, checked once (PipeWire 1.0 has no --container)."""
    global _raw_args
    if _raw_args is None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pw-record", "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
        except OSError:
            return []  # pw-record is missing; capture reports that itself
        _raw_args = raw_output_args(out.decode(errors="replace"))
    return _raw_args


async def capture_audio(
    device: str,
    rate: int = SAMPLE_RATE,
    chunk_samples: int = CHUNK_SAMPLES,
    stall_s: float = 5.0,
) -> AsyncGenerator[np.ndarray, None]:
    """Yields float32 audio chunks from a PipeWire source using pw-record; ends if pw-record exits or stalls."""
    chunk_bytes = chunk_samples * 4
    # Without raw output, newer pw-record writes a file header that would be read as garbage audio (including a NaN).
    args = [
        "pw-record", *await _pw_record_raw_args(),
        "--rate", str(rate), "--channels", "1", "--format", "f32",
    ]
    # Record a monitor by targeting its sink; the .monitor name makes pw-record fall back to the mic when the sink is idle.
    if device.endswith(".monitor"):
        args += ["-P", "stream.capture.sink=true", "--target", device[: -len(".monitor")]]
    else:
        args += ["--target", device]
    args.append("-")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_die_with_parent,  # reap pw-record if the engine dies ungracefully
    )
    assert proc.stdout is not None
    exited = False
    try:
        while True:
            try:
                data = await asyncio.wait_for(proc.stdout.readexactly(chunk_bytes), stall_s)
            except TimeoutError:
                # pw-record sends silence as zeros, so no data at all means it's stuck.
                _log.warning("pw-record sent no audio for %.0fs; restarting capture", stall_s)
                return
            yield np.frombuffer(data, dtype=np.float32).copy()
    except asyncio.IncompleteReadError:
        exited = True
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # pw-record already exited (e.g. on shutdown)
        await proc.wait()
        if exited and proc.stderr is not None:
            detail = (await proc.stderr.read()).decode(errors="replace").strip()
            _log.warning("pw-record stopped (exit code %s): %s", proc.returncode, detail[-300:] or "no error output")
