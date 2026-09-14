"""Routes one application's audio into a private null sink so capture hears only that app."""

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
# Latency of the loopback that plays captured audio back to the speakers.
_LOOPBACK_LATENCY_MS = 50


@dataclass(frozen=True)
class PlayingApp:
    """An application currently producing audio."""

    name: str  # application.name (stream nodes come and go)
    streams: tuple[int, ...]  # object.serial values (pactl's "index")


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
    """Like _run, but returns False instead of raising (streams can vanish mid-move)."""
    try:
        await _run(*args)
    except (RuntimeError, FileNotFoundError, OSError) as exc:  # noqa: BLE001
        _log.debug("ignoring failure of %s: %s", " ".join(args), exc)
        return False
    return True


def _apps_from(objects: list[Any]) -> list[PlayingApp]:
    """Playing applications from a `pw-dump`, with each app's streams grouped together."""
    by_name: dict[str, list[int]] = {}
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        props = ((obj.get("info") or {}).get("props")) or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        node = str(props.get("node.name") or "")
        # Skip our own loopback nodes, which have no application.name.
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
        # Skip names with other escapes; they aren't app names a user would recognise.
        if name and "\\" not in name:
            names.append(name)
    return sorted(set(names))


def known_apps() -> list[str]:
    """App names from WirePlumber's state file, including apps that aren't playing now."""
    try:
        return _names_from_state(_STATE_FILE.read_text())
    except OSError as exc:
        _log.debug("no wireplumber stream state (%s); live apps only", exc)
        return []


async def list_apps() -> list[PlayingApp]:
    """Applications currently playing audio, from `pw-dump`."""
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
    # Checked separately: the sink can exist without its loopback after an interrupted teardown.
    if not await _module_ids(f"source={MONITOR}"):
        await _run(
            "pactl", "load-module", "module-loopback",
            f"source={MONITOR}",
            f"sink={default_sink}",
            f"latency_msec={_LOOPBACK_LATENCY_MS}",
        )


async def cleanup(default_sink: str | None = None) -> None:
    """Moves captured streams back to `default_sink` and unloads our modules; safe if none are loaded."""
    index = await _sink_index()
    if index is not None and default_sink:
        # Move streams back explicitly so audio never lands somewhere unexpected.
        for stream in await _streams_in_sink(index):
            await _try("pactl", "move-sink-input", stream, default_sink)
    for needle in (f"source={MONITOR}", f"sink_name={SINK_NAME}"):
        for module in await _module_ids(needle):
            await _try("pactl", "unload-module", module)


async def reconcile(app: str) -> str:
    """Sets up capture for `app` (empty = everything) and returns the device to capture from."""
    default_sink = await get_default_sink()
    if not app:
        await cleanup(default_sink)
        return f"{default_sink}.monitor"

    await _ensure_sink(default_sink)
    moved = await move_streams(app)
    await evict(app, default_sink)
    if moved == 0:
        # The app may just be silent; watch() moves its streams when they appear.
        _log.info("%r is not playing yet; waiting for it to start", app)
    return MONITOR


def _foreign(in_sink: list[int], ours: set[int]) -> list[int]:
    """Streams sitting in our sink that do not belong to the selected app."""
    return [s for s in in_sink if s not in ours]


async def evict(app: str, default_sink: str) -> int:
    """Moves every stream that isn't `app` out of our sink; returns how many were moved."""
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
    """Moves `app`'s new streams into the null sink as they appear, until cancelled."""
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
            # e.g. "Event 'new' on sink-input #4497"; only new streams need moving.
            line = raw.decode(errors="replace")
            if "'new'" in line and "sink-input" in line:
                await move_streams(app)
                # WirePlumber can route a new foreign stream into our sink; push it back out.
                await evict(app, default_sink)
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        await proc.wait()
