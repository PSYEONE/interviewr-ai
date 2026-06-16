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
- [ ] Extend enumeration to individual application **sink-inputs** (needs node-level
      targeting, not just source names) and expose targets via the API.
- [ ] Support capturing a **specific application's** output stream (pick the browser /
      Zoom / Meet), not the global monitor.
- [ ] Support a **virtual null sink** mode: route the call app into a dedicated null sink,
      capture its monitor, and mirror it back to real speakers (combine sink) so the user
      still hears it. Fully isolates call audio from mic/notifications/other apps.
- [ ] Surface the selector in the Tauri overlay UI once it exists.
- [ ] Document the mic-sidetone gotcha and the recommended capture mode.
