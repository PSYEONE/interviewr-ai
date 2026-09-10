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
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capture import get_default_sink

_log = logging.getLogger(__name__)

SINK_NAME = "interviewr"
MONITOR = f"{SINK_NAME}.monitor"

_STATE_FILE = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    / "wireplumber/stream-properties"
)
_STATE_PREFIX = "Output/Audio:application.name:"
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


async def _try(*args: str) -> bool:
    """Best-effort variant for paths where a missing object is not an error. Returns success.

    Callers that count outcomes must use the return value: a stream serial read from one pw-dump
    can already be gone by the time the move runs, which browsers do constantly.
    """
    try:
        await _run(*args)
    except (RuntimeError, FileNotFoundError, OSError) as exc:  # noqa: BLE001
        _log.debug("ignoring failure of %s: %s", " ".join(args), exc)
        return False
    return True


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
        node = str(props.get("node.name") or "")
        # module-loopback's own nodes carry no application.name, so they would otherwise be
        # offered as "output.loopback-2784-13". Choosing one would route our loopback into the
        # sink it reads from.
        if node.startswith(("output.loopback", "input.loopback")):
            continue
        name = props.get("application.name") or node
        serial = props.get("object.serial")
        if not name or serial is None:
            continue
        by_name.setdefault(str(name), []).append(int(serial))
    return [PlayingApp(name=n, streams=tuple(sorted(s))) for n, s in sorted(by_name.items())]


def _names_from_state(text: str) -> list[str]:
    """Application names out of WirePlumber's stream-properties file."""
    names = []
    for line in text.splitlines():
        key, sep, _ = line.partition("=")
        if not sep or not key.startswith(_STATE_PREFIX):
            continue
        name = key[len(_STATE_PREFIX) :].replace(r"\s", " ")
        # Entries can carry escapes beyond \s (one real example is "PipeWire\sALSA\s\oresolve\c").
        # Those are not app names a user would recognise, so drop rather than half-decode them.
        if name and "\\" not in name:
            names.append(name)
    return sorted(set(names))


def known_apps() -> list[str]:
    """Applications that have played audio on this machine before, whether playing now or not.

    PipeWire has no node for a silent application, so the live list alone cannot offer a browser
    that is merely open. WirePlumber remembers per-application volume, which incidentally makes
    its state file a record of what plays audio here. Read for names only: it is another
    project's internal state, so a missing or changed file degrades to the live list.
    """
    try:
        return _names_from_state(_STATE_FILE.read_text())
    except OSError as exc:
        _log.debug("no wireplumber stream state (%s); live apps only", exc)
        return []


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
    moved = await move_streams(app)
    await evict(app, default_sink)
    if moved == 0:
        # Normal: the app may be silent right now. watch() moves its streams the moment they
        # appear, so this is a wait rather than a failure.
        _log.info("%r is not playing yet; waiting for it to start", app)
    return MONITOR


def _foreign(in_sink: list[int], ours: set[int]) -> list[int]:
    """Streams sitting in our sink that do not belong to the selected app."""
    return [s for s in in_sink if s not in ours]


async def evict(app: str, default_sink: str) -> int:
    """Send everything that is not `app` back to `default_sink`. Returns how many were evicted.

    The invariant this maintains is the feature: only the selected app's streams live in our sink.
    Two things break it otherwise. Switching the selection used to leave the previous app behind,
    so a capture contained both. And WirePlumber remembers a target after a move under keys wider
    than the application (a media.role entry will pull in any stream with that role), so foreign
    streams can arrive with no move from us at all.
    """
    index = await _sink_index()
    if index is None:
        return 0
    ours = {s for a in await list_apps() if a.name == app for s in a.streams}
    evicted = 0
    for stream in _foreign([int(s) for s in await _streams_in_sink(index)], ours):
        if await _try("pactl", "move-sink-input", str(stream), default_sink):
            _log.info("moved stream %s out of the capture sink; it is not %r", stream, app)
            evicted += 1
    return evicted


async def move_streams(app: str) -> int:
    """Move every current stream of `app` into the null sink. Returns how many were moved."""
    moved = 0
    for playing in await list_apps():
        if playing.name != app:
            continue
        for stream in playing.streams:
            if await _try("pactl", "move-sink-input", str(stream), SINK_NAME):
                moved += 1
            else:
                _log.info("stream %s of %r vanished before it could be moved", stream, app)
    return moved


async def watch(app: str) -> None:
    """Move `app`'s streams into the null sink as they appear, until cancelled.

    Needed because a moved stream's target is not remembered: WirePlumber's state stores volume
    and mute only, so a replacement stream goes back to the default sink. Chromium browsers
    destroy and recreate their output stream on pause, seek and tab change, which made a
    move-once approach lose the audio within seconds.
    """
    if not app:
        return
    default_sink = await get_default_sink()
    proc = await asyncio.create_subprocess_exec(
        "pactl", "subscribe",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    try:
        async for raw in proc.stdout:
            # "Event 'new' on sink-input #4497". Only 'new' matters; 'change' fires constantly
            # for volume and format updates on streams already where we want them.
            line = raw.decode(errors="replace")
            if "'new'" in line and "sink-input" in line:
                await move_streams(app)
                # A new stream can land in our sink without us moving it, when WirePlumber has a
                # remembered target that matches it. Push it back out before it is captured.
                await evict(app, default_sink)
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        await proc.wait()
