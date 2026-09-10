"""Null-sink routing: parsing pw-dump into playable apps, on fixtures (no real audio server)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.audio import routing
from app.audio.routing import (
    MONITOR,
    SINK_NAME,
    PlayingApp,
    _apps_from,
    _foreign,
    _names_from_state,
)

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


def test_our_own_loopback_is_not_offered() -> None:
    """module-loopback's nodes have no application.name, so they used to be listed as
    "output.loopback-2784-13". Selecting one would route our loopback into the sink it reads."""
    dump: list[Any] = [
        {"id": 9, "info": {"props": {"media.class": "Stream/Output/Audio",
                                     "node.name": "output.loopback-2784-13",
                                     "object.serial": 31}}},
        {"id": 10, "info": {"props": {"media.class": "Stream/Output/Audio",
                                      "node.name": "input.loopback-2784-14",
                                      "object.serial": 32}}},
    ]
    assert _apps_from(dump) == []


# Real lines from ~/.local/state/wireplumber/stream-properties, values trimmed.
_STATE = r"""[stream-properties]
Audio/Sink:node.name:alsa_output.pci-0000_01_00.1.pro-output-3={"volume":1.0}
Output/Audio:application.name:Brave={"volume":1.000000, "mute":false}
Output/Audio:application.name:Chromium={"volume":1.000000, "mute":false}
Output/Audio:application.name:FL\sStudio={"volume":1.000000, "mute":false}
Output/Audio:application.name:PipeWire\sALSA\s\oresolve\c={"volume":1.000000}
Output/Audio:media.role:Music={"volume":1.000000}
Input/Audio:application.name:Brave\sinput={"volume":1.000000}
"""


def test_known_apps_reads_output_streams_only() -> None:
    # Sinks, mic inputs and media.role entries are not applications the user can capture.
    assert _names_from_state(_STATE) == ["Brave", "Chromium", "FL Studio"]


def test_known_apps_is_empty_without_the_state_file(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-WirePlumber setup must degrade to the live list, not raise.
    monkeypatch.setattr(routing, "_STATE_FILE", Path("/nonexistent/stream-properties"))
    assert routing.known_apps() == []


def test_foreign_streams_are_the_ones_to_evict() -> None:
    """Switching the selected app used to leave the previous one in the sink, so a capture
    contained both and the second selection only appeared to work."""
    assert _foreign([11, 22, 33], ours={22}) == [11, 33]


def test_nothing_to_evict_when_the_sink_holds_only_ours() -> None:
    assert _foreign([22, 23], ours={22, 23}) == []
    assert _foreign([], ours={22}) == []


def test_monitor_name_matches_the_sink() -> None:
    # capture_audio strips ".monitor" and targets the sink itself, so these must agree.
    assert MONITOR == f"{SINK_NAME}.monitor"
