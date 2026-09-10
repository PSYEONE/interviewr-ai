# TODO

## Selectable audio capture in the engine

By default `app/audio/capture.py` captures the **default sink's monitor** (whole-system
output), which grabs everything: notification dings, music, every app's audio.

An earlier note here claimed hardware mic sidetone folds the user's own voice into that
monitor on the maintainer's laptop. **Not reproduced.** Card 0 exposes no `Loopback`,
`Sidetone` or `Mic Playback` control, and the default sink's monitor measures
`rms=0.000000, peak=0.000000` with nothing playing, where ambient noise would show if the
mic fed the playback path. It stays possible on an analog codec configured for it; treat it
as an unverified hardware quirk, not a reason this feature exists.

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
- [x] Document the recommended capture mode in the README ("Choosing what gets captured"),
      including that an isolated capture contains the far side of a call and not the user.
- [x] Re-route a stream that appears **mid-interview**: `routing.watch()` reads
      `pactl subscribe` for the length of a capture session and moves the chosen app's new
      streams in. Needed because a browser recreates its output stream on pause/seek/tab
      change, and because an app selected while silent has no stream to move at start.
- [x] Pick an app that is not currently playing: `known_apps()` reads the names WirePlumber
      has stored state for, so a silent browser is still selectable.
