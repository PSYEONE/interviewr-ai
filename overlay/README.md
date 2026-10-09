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

- **Answer / Clear / Reset**, the same WS commands the engine already exposes.
- **Pin** toggles always-on-top (uses the KWin rule on KDE/Wayland; the native call elsewhere).
- Drag the top bar to move; resize from the edges.
- **&#10005;** quits.

(Click-through is intentionally not exposed yet: on Wayland it engages but cannot be
un-toggled from a window you can no longer click. It will return later behind a global hotkey.)

## Always-on-top (KDE/KWin)

GTK can't pin a window on Wayland (keep-above is ignored), so on KDE the app writes a KWin
"keep above" window rule itself. The **Pin** button toggles it and the choice persists across
restarts, so there is no manual setup. Unpinned by default.

If pinning has no effect, the window class differs from the expected `interviewr-ai`; check
it via System Settings > Window Management > Window Rules > "Detect Window Properties" with the
overlay focused.

Only KDE is wired up for now. GNOME/Sway/Hyprland and a portable XWayland fallback come later.

## Platform notes

- **Click-through** is not exposed yet (on Wayland it traps input with no escape; returns later
  behind a global hotkey).
- **Screen-capture exclusion** works on KDE Plasma 6.6+ via the `excludefromcapture` KWin rule and
  a transient KWin script (the **Hide from Screencast** toggle; the button is disabled below 6.6).
  Only PipeWire-portal capturers honor it, and the cursor is composited separately, so treat it as a
  privacy convenience rather than an undetectability guarantee. No GNOME or wlroots equivalent.
- The Linux webview is **WebKitGTK, not Chromium**, test UI changes here, not in Chrome.
- If the window renders opaque instead of translucent, that is a compositor/transparency
  limitation, not a bug in the client.

To replace the placeholder icon, drop a 512px PNG and run `cargo tauri icon path/to/logo.png`.
