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

- **Always-on-top:** works on KWin/Wayland.
- **Click-through (input passthrough):** unreliable on Wayland; fine on X11. Tauri exposes
  the call (`set_ignore_cursor_events`) but the compositor decides.
- **Screen-capture exclusion:** **not available on Wayland.** It's a Windows/macOS feature
  only. Treat it as a Win/Mac-only enhancement, not a Linux promise.
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
