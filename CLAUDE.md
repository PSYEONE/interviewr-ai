# CLAUDE.md - context for Claude Code

Read this first. It captures the decisions already made so you don't re-derive or
contradict them.

## What this is

An open-source, **Linux-first, privacy-first** real-time interview/meeting copilot.
It captures system audio, transcribes live, detects questions, retrieves context
(RAG over resume + job description), and streams an LLM answer into a native overlay.

**Ethical framing is part of the product, not a disclaimer.** Copy and docs position it
for live **prep**, **accessibility** (real-time captions for hard-of-hearing users, ESL /
neurodivergent support), and private **meeting notes**, not deception. Keep this framing
in all product copy.

## Maintainer environment (assume this competence; no beginner hand-holding)

- Kubuntu on **Wayland (KWin/KDE Plasma)**.
- Primary GPU: **AMD Radeon RX 7900 XT** (desktop). Laptop (Razer Blade 15) has NVIDIA.
- Hetzner VPS in Helsinki; Raspberry Pi for bots. Strong Python, Linux/systemd,
  PostgreSQL, API integration. Comfortable with crypto infra. GitHub: PSYEONE.

## The one architectural rule

**Python/FastAPI is the engine. Every UI is just a client of it.**

```
[PipeWire system audio] -> capture -> STT -> question detection
      -> RAG (resume + job desc + history) -> LLM (streamed)
      -> broadcast over AUTHED WebSocket -> N subscribers
```

Clients: (1) Tauri overlay = primary, same machine, full capability;
(2) phone = **read-only viewer** over LAN/relay; (3) browser tab = optional degraded mode.

The backend **broadcasts events to a set of subscribers and never assumes a single
client.** Do not let overlay-specific or transport-specific logic leak into the engine.
That's what keeps phone/browser clients nearly free to add later.

## Locked decisions

- **UI shell:** Tauri (web UI, Svelte/Solid/plain HTML, in a native window). Not Electron
  (size/RAM), not a browser tab (can't do overlay or reliable system-audio capture).
- **STT engine:** **whisper.cpp with the Vulkan backend.** Cross-vendor GPU accel
  (AMD/NVIDIA/Intel/Apple), no CUDA/ROCm install. CPU auto-fallback.
  - Do **not** use `faster-whisper` (CTranslate2) or NVIDIA Parakeet. Both are
    **CUDA-only** and will silently ignore the maintainer's AMD GPU.
- **Audio capture:** `pw-record` subprocess (not sounddevice). PortAudio on Ubuntu is
  too old for PipeWire hostapi. pw-record pipes raw PCM to stdout.
- **STT/LLM are adapters from day one.** Providers are config-switchable
  (`STTProvider` / `LLMProvider` enums in `app/config.py`).
- **Default pipeline:** local STT (whisper.cpp/Vulkan) + local LLM (Ollama).
  Everything stays on-machine by default.
  - Cloud option: BYOK Anthropic/OpenAI/Gemini for LLM.
  - Cloud STT option: Groq/Deepgram, no model download needed.
- **Security:** keys from `.env` only, never hardcoded (git history is forever). WS
  requires an auth token. Bind `127.0.0.1` by default; `0.0.0.0` only when
  `enable_remote_viewer=True`, which `config.py` refuses without a strong token.
- **License:** AGPL-3.0 (fetch canonical text, see `LICENSE`).

## Platform reality (don't promise what the compositor won't give)

- **Always-on-top:** the toolkit call (`set_always_on_top`) is a **no-op on native Wayland**
  (GTK ignores keep-above); an app cannot pin itself. On KDE the overlay writes a KWin window rule
  to force it (`ensure_kwin_rule` / `set_kwin_rule_bool` in `overlay/src-tauri/src/lib.rs`, driven by
  the Pin toggle). Works natively on X11/Windows/macOS.
- **Click-through (input passthrough):** unreliable on Wayland; fine on X11. Tauri exposes
  the call (`set_ignore_cursor_events`) but the compositor decides.
- **Screen-capture exclusion:** KDE-only. Two KWin layers exist: (1) the **runtime**
  `excludeFromCapture` Window property, scriptable since **KWin 6.6** (verified in `window.h`:
  `Q_PROPERTY(... WRITE setExcludeFromCapture ...)`), which is what the "Hide from Screencast"
  menu action sets; (2) the **persistent** `excludefromcapture` *window rule*, only since **6.7**
  (absent from `Plasma/6.6` `rulesettings.kcfg`, present in `Plasma/6.7`). The overlay drives layer
  (1) so it works on 6.6: the "Hide" toggle runs a transient KWin script over D-Bus
  (`kwin_run_script` / `exclude_capture_script` in `overlay/src-tauri/src/lib.rs`) that flips the
  property on the window matched by `OVERLAY_WMCLASS`. Opt-in, **gated on KWin >= 6.6**
  (`capture_supported`; button disabled below that). The preference is **persisted** by writing the
  layer-(2) `excludefromcapture` rule key (`KWIN_CAPTURE_KEY`): inert-but-remembered on 6.6, honored
  natively on 6.7+. On 6.6 the runtime property is lost when the window is recreated, so the
  frontend re-runs the script at startup to re-apply the stored choice. The **cursor** is composited
  into the capture separately from the window, and the overlay deliberately does **not** try to
  alter it: in-app cursor tricks were tried and removed (a CSS arrow over our surface only covered
  hover/buttons; `setResizable`-while-hidden to kill the resize-edge cursor is as unreliable on
  Wayland as `set_always_on_top`, doesn't apply at startup, doesn't restore on toggle; and the
  title-bar move cursor is compositor-drawn during the interactive-move grab, with no in-app fix:
  manual drag is impossible since clients can't position their own toplevels, and XWayland was
  rejected for HiDPI/window-mgmt regressions). So the cursor shows normally; for **true** stealth the
  user applies a uniform cursor theme (System Settings → Cursors, all shapes = the arrow) while
  sharing, system-wide, a deliberate "while sharing" choice the overlay can't scope to itself (a
  good candidate for an end-user notice when the feature is documented). **No** GNOME/Mutter or
  wlroots/Hyprland equivalent. Only **PipeWire-portal** capturers honor it
  (Spectacle recording, OBS-PipeWire, browser WebRTC, Zoom/Meet, and, confirmed, Discord);
  non-portal X11 grabs do not. Privacy/accessibility convenience, **not** an undetectability
  guarantee (also defeated by a phone camera). Document/market as "KDE Plasma 6.6+", not generic
  Linux. Separately, Windows/macOS have a real exclusion via Tauri `set_content_protected` (a
  Win/Mac-only path, untestable on the maintainer's machine).
- **WebView differences:** Linux Tauri uses **WebKitGTK**, not Chromium. Test the UI inside the
  actual Tauri webview, not just Chrome. APIs/rendering can differ.

## Phase 0 goal (do this first)

Prove the loop end-to-end on AMD hardware, terminal only, no UI:
capture system audio (PipeWire) > whisper.cpp (Vulkan) transcription >
stream to Ollama LLM > print answer to terminal.
No provider abstraction yet beyond what `config.py` already has. Hardcode the path,
make it work, then generalize in Phase 2.

## Verify with your tools before asserting (don't trust this file's memory)

- Current whisper.cpp Vulkan build flags / `pywhispercpp` Vulkan support.
- Exact PipeWire monitor-source capture approach for the maintainer's setup.
- Current Ollama API format for `/api/chat` streaming.

## Coding standards

Typed Python (mypy strict), real error handling, no placeholder stubs unless asked for a
skeleton. When a task spans multiple files, present the file tree first, then the files.
Be direct. If a choice here is wrong, say so and explain the tradeoff.
No em dashes. No comment blocks. Keep comments concise.
