"""Route one application's audio into a private null sink, so capture hears only it.

Capturing the default sink's monitor picks up everything: notification dings, music, every tab.
On hardware with mic sidetone (Intel SOF/HDA) it also folds the user's own voice into the playback
path, so the copilot tries to answer the user.

The fix is a null sink the engine owns: move the chosen app's playback into it, loop it back to the
real sink so the user still hears the call, and capture the null sink's monitor. Nothing else is
mixed in, and the user's mic never reaches an application's *output*, so sidetone is gone too.

Everything here is idempotent. `reconcile` is the only entry point the engine calls; it converges
on the requested state whatever the current one is, which is also what makes recovery after a crash
just another call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .capture import get_default_sink

_log = logging.getLogger(__name__)

SINK_NAME = "interviewr"
MONITOR = f"{SINK_NAME}.monitor"
# What the user hears is delayed by this much. Low enough not to be distracting in a call, high
# enough not to crackle.
_LOOPBACK_LATENCY_MS = 50


@dataclass(frozen=True)
class PlayingApp:
    """An application currently producing audio."""

    name: str  # application.name: the durable handle, since stream nodes come and go
    streams: tuple[int, ...]  # object.serial values, which is what pactl calls an index


async def _run(*args: str) -> str:
    """Run a PipeWire CLI tool, returning stdout. Raises on failure."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {stderr.decode().strip()}")
    return stdout.decode()


async def _try(*args: str) -> None:
    """Best-effort variant, for teardown paths where a missing object is not an error."""
    try:
        await _run(*args)
    except (RuntimeError, FileNotFoundError, OSError) as exc:  # noqa: BLE001 - teardown only
        _log.debug("ignoring failure of %s: %s", " ".join(args), exc)


def _apps_from(objects: list[Any]) -> list[PlayingApp]:
    """Playing applications from a `pw-dump`, one entry per app however many streams it has.

    A browser can hold several output streams at once, and all of them have to move together or
    half the call stays on the default sink.
    """
    by_name: dict[str, list[int]] = {}
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        props = ((obj.get("info") or {}).get("props")) or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        name = props.get("application.name") or props.get("node.name")
        serial = props.get("object.serial")
        if not name or serial is None:
            continue
        by_name.setdefault(str(name), []).append(int(serial))
    return [PlayingApp(name=n, streams=tuple(sorted(s))) for n, s in sorted(by_name.items())]


async def list_apps() -> list[PlayingApp]:
    """Applications currently playing audio.

    Read from `pw-dump` rather than `pactl list short sink-inputs`, which carries no application
    name at all. Note the id used everywhere below is `object.serial`, not the PipeWire node id:
    pactl's "index" is the serial, and passing a node id would move the wrong stream or nothing.
    """
    try:
        raw = await _run("pw-dump")
    except (RuntimeError, FileNotFoundError, OSError) as exc:  # noqa: BLE001
        _log.warning("could not list playing applications: %s", exc)
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("pw-dump returned invalid JSON: %s", exc)
        return []
    return _apps_from(parsed if isinstance(parsed, list) else [])


async def _module_ids(needle: str) -> list[str]:
    """Loaded module ids whose arguments mention `needle`. Ours are found by sink name."""
    try:
        raw = await _run("pactl", "list", "short", "modules")
    except (RuntimeError, FileNotFoundError, OSError):  # noqa: BLE001
        return []
    ids = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and needle in fields[2]:
            ids.append(fields[0])
    return ids


async def _sink_index() -> str | None:
    """pactl's index for our null sink, or None when it does not exist."""
    try:
        raw = await _run("pactl", "list", "short", "sinks")
    except (RuntimeError, FileNotFoundError, OSError):  # noqa: BLE001
        return None
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == SINK_NAME:
            return fields[0]
    return None


async def _streams_in_sink(index: str) -> list[str]:
    """Sink-input indexes currently attached to the sink at `index`."""
    try:
        raw = await _run("pactl", "list", "short", "sink-inputs")
    except (RuntimeError, FileNotFoundError, OSError):  # noqa: BLE001
        return []
    return [
        fields[0]
        for line in raw.splitlines()
        if len(fields := line.split("\t")) >= 2 and fields[1] == index
    ]


async def _ensure_sink(default_sink: str) -> None:
    """Create the null sink and its loopback if they are not already loaded."""
    if not await _module_ids(f"sink_name={SINK_NAME}"):
        await _run(
            "pactl", "load-module", "module-null-sink",
            f"sink_name={SINK_NAME}",
            f"sink_properties=device.description={SINK_NAME}",
        )
    # Separate check: the sink can exist without the loopback if teardown was interrupted, and
    # without it the user hears nothing at all.
    if not await _module_ids(f"source={MONITOR}"):
        await _run(
            "pactl", "load-module", "module-loopback",
            f"source={MONITOR}",
            f"sink={default_sink}",
            f"latency_msec={_LOOPBACK_LATENCY_MS}",
        )


async def cleanup(default_sink: str | None = None) -> None:
    """Return any captured streams to `default_sink` and unload our modules.

    Safe to call when nothing is loaded, which is why the engine calls it on startup too: a crashed
    engine leaves the sink behind, and the user should not have to unload it by hand.
    """
    index = await _sink_index()
    if index is not None and default_sink:
        # Move streams back explicitly rather than relying on the compositor's fallback when a
        # sink disappears, so the user's audio never lands somewhere unexpected.
        for stream in await _streams_in_sink(index):
            await _try("pactl", "move-sink-input", stream, default_sink)
    for needle in (f"source={MONITOR}", f"sink_name={SINK_NAME}"):
        for module in await _module_ids(needle):
            await _try("pactl", "unload-module", module)


async def reconcile(app: str) -> str:
    """Converge on capturing `app` (empty = everything) and return the device to capture from.

    Called before every capture start, which is what keeps a relaunched app routed: WirePlumber
    does not remember per-application targets, so a moved stream is not restored on the app's next
    launch and re-routing has to happen each time.
    """
    default_sink = await get_default_sink()
    if not app:
        await cleanup(default_sink)
        return f"{default_sink}.monitor"

    await _ensure_sink(default_sink)
    moved = 0
    for playing in await list_apps():
        if playing.name != app:
            continue
        for stream in playing.streams:
            await _try("pactl", "move-sink-input", str(stream), SINK_NAME)
            moved += 1
    if moved == 0:
        # Not fatal: the app may simply be silent right now, and it will be picked up the next
        # time capture starts. Say so rather than leaving a silent transcript unexplained.
        _log.warning("%r is not playing anything; capturing its (silent) sink", app)
    return MONITOR
