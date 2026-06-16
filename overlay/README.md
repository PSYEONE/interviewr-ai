# interviewr overlay

The primary UI: a frameless, always-on-top native window (Tauri 2 + WebKitGTK) whose webview
connects to the running backend over the authed WebSocket and renders the live transcript and
streamed answers. It is a thin client; all logic lives in the Python engine.

## Prerequisites (one-time)

Rust and Node are assumed. Install the Tauri CLI and the Linux build headers:

```bash
cargo install tauri-cli --version "^2"

# Kubuntu / Debian build deps (runtime libs are present; the -dev headers are not):
sudo apt install libwebkit2gtk-4.1-dev libsoup-3.0-dev librsvg2-dev \
  libjavascriptcoregtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev pkg-config
```

Verify: `cargo tauri --version` and `pkg-config --modversion webkit2gtk-4.1` both succeed.

## Run (dev)

1. Start the backend (separate terminal):
   ```bash
   cd ../backend && .venv/bin/python run_pipeline.py
   ```
2. Launch the overlay:
   ```bash
   cd overlay && cargo tauri dev
   ```

The overlay reads `INTERVIEWR_HOST` / `INTERVIEWR_PORT` / `INTERVIEWR_AUTH_TOKEN` from real env
vars, falling back to the project-root `.env` (the same one the backend uses), so on one machine
it connects with no config. It auto-reconnects, so the launch order does not matter.

## Build

```bash
cargo tauri build   # produces a .deb / AppImage under src-tauri/target/release/bundle/
```

## Controls

- **Answer / Clear / Reset** — the same WS commands the engine already exposes.
- Drag the top bar to move; resize from the edges.
- **&#10005;** quits.

(Click-through is intentionally not exposed yet: on Wayland it engages but cannot be
un-toggled from a window you can no longer click. It will return later behind a global hotkey.)

## Platform notes (Wayland/KWin)

- Always-on-top works.
- **Click-through** is unreliable on Wayland; the toggle is best-effort.
- **Screen-capture exclusion** is not available on Wayland (Windows/macOS only, later).
- The Linux webview is **WebKitGTK, not Chromium** — test UI changes here, not in Chrome.
- If the window renders opaque instead of translucent, that is a compositor/transparency
  limitation, not a bug in the client.

To replace the placeholder icon, drop a 512px PNG and run `cargo tauri icon path/to/logo.png`.
