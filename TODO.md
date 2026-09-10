# TODO

## Selectable audio capture in the engine

Right now `app/audio/capture.py` captures the **default sink's monitor** (whole-system
output). That's the wrong long-term strategy:

- It grabs everything: notification dings, music, every app's audio.
- On some hardware (e.g. the maintainer's Intel SOF/HDA laptop) the mic is folded into the
  playback path via **hardware mic sidetone**, so the monitor also picks up your own voice.
  An interview copilot then tries to "answer" the user's own words.

Build a capture-source selector so the user captures **only the call/meeting audio**:

- [x] Enumerate capture sources (sink monitors + mics) via `list_capture_targets()` in
      `app/audio/capture.py`; list them from the CLI with `run_pipeline.py --list-devices`.
      Selection works today by setting `INTERVIEWR_AUDIO_DEVICE` to a listed id.
- [x] Enumerate playing applications via `list_apps()` in `app/audio/routing.py` (from
      `pw-dump`, which carries `application.name`; `pactl list short sink-inputs` does not),
      exposed as `GET /audio/apps`.
- [x] Capture a **specific application's** output, via the null sink below rather than by
      targeting its stream node. Targeting the node directly does work
      (`pw-record --target <serial> -P stream.capture.sink=true`) but the node disappears
      whenever the app goes silent, which would kill capture mid-interview.
- [x] **Virtual null sink** mode: `reconcile()` loads `module-null-sink` + `module-loopback`
      (so the user still hears the call), moves the chosen app's streams in, and captures
      `interviewr.monitor`. Idempotent, and `cleanup()` runs on engine start as well as
      shutdown so a crash cannot leave a sink behind. Verified spectrally: with two apps
      playing different tones, isolating one attenuates the other by ~1.4e6.
- [x] Selector in the overlay UI: Settings > Audio > "Capture from". A cheap field, so it
      applies at the next interview start with no engine restart.
- [ ] Document the mic-sidetone gotcha and the recommended capture mode in the README.
- [x] Re-route a stream that appears **mid-interview**: `routing.watch()` reads
      `pactl subscribe` for the length of a capture session and moves the chosen app's new
      streams in. Needed because a browser recreates its output stream on pause/seek/tab
      change, and because an app selected while silent has no stream to move at start.
- [x] Pick an app that is not currently playing: `known_apps()` reads the names WirePlumber
      has stored state for, so a silent browser is still selectable.
