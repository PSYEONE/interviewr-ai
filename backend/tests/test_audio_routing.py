"""Null-sink routing: parsing pw-dump into playable apps, on fixtures (no real audio server)."""

from __future__ import annotations

import json
from typing import Any

from app.audio.routing import MONITOR, SINK_NAME, PlayingApp, _apps_from

# Real `pw-dump` node entries, trimmed to the props the parser reads. Two streams from one
# application, which is the browser case: several tabs, one app, all of which must move together.
_DUMP = json.loads("""
[
 {"id": 72, "type": "PipeWire:Interface:Node", "info": {"props": {
   "media.class": "Stream/Output/Audio", "application.name": "Chromium",
   "node.name": "Chromium", "object.serial": 3994}}},
 {"id": 87, "type": "PipeWire:Interface:Node", "info": {"props": {
   "media.class": "Stream/Output/Audio", "application.name": "Chromium",
   "node.name": "Chromium", "object.serial": 3971}}},
 {"id": 91, "type": "PipeWire:Interface:Node", "info": {"props": {
   "media.class": "Stream/Output/Audio", "application.name": "Spotify",
   "node.name": "spotify", "object.serial": 4002}}},
 {"id": 137, "type": "PipeWire:Interface:Node", "info": {"props": {
   "media.class": "Audio/Sink", "node.name": "alsa_output.pci-0000_00_1f.3", "object.serial": 12}}},
 {"id": 56, "type": "PipeWire:Interface:Node", "info": {"props": {
   "media.class": "Audio/Source", "node.name": "alsa_input.pci-0000_00_1f.3",
   "object.serial": 9}}}
]
""")


def test_groups_every_stream_of_one_app() -> None:
    apps = _apps_from(_DUMP)
    assert apps == [
        PlayingApp(name="Chromium", streams=(3971, 3994)),
        PlayingApp(name="Spotify", streams=(4002,)),
    ]


def test_ignores_sinks_and_sources() -> None:
    # Only Stream/Output/Audio is an application playing something; a sink or a mic is not.
    assert all(a.name not in {"alsa_output.pci-0000_00_1f.3"} for a in _apps_from(_DUMP))


def test_nothing_playing_is_normal() -> None:
    # The common case: PipeWire has no node for a silent app, so the picker is simply empty.
    assert _apps_from([]) == []


def test_survives_entries_without_props() -> None:
    # pw-dump carries many object types; some have no info/props at all.
    junk: list[Any] = [{"id": 1}, {"id": 2, "info": {}}, {"id": 3, "info": {"props": {}}}, "x"]
    assert _apps_from(junk) == []


def test_falls_back_to_node_name_when_unnamed() -> None:
    dump: list[Any] = [
        {"id": 5, "info": {"props": {"media.class": "Stream/Output/Audio",
                                     "node.name": "pw-play", "object.serial": 7}}}
    ]
    assert _apps_from(dump) == [PlayingApp(name="pw-play", streams=(7,))]


def test_a_stream_without_a_serial_is_skipped() -> None:
    # The serial is what pactl calls an index; without it the stream cannot be moved, so listing
    # it would offer the user something that silently does nothing.
    dump: list[Any] = [
        {"id": 5, "info": {"props": {"media.class": "Stream/Output/Audio", "node.name": "x"}}}
    ]
    assert _apps_from(dump) == []


def test_monitor_name_matches_the_sink() -> None:
    # capture_audio strips ".monitor" and targets the sink itself, so these must agree.
    assert MONITOR == f"{SINK_NAME}.monitor"
