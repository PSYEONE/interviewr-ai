# interviewr-ai

**The interview/meeting copilot that actually runs on Linux, keeps your data on your
machine, and is built to _help_, not to cheat.**

interviewr-ai listens to your interviews and meetings, transcribes them in real time, and
surfaces AI-generated answers and notes in a native always-on-top overlay. It is
**Linux-first**, **privacy-first**, and **subscription-free**.

- **Linux-first** - native PipeWire/Wayland audio capture, not an afterthought.
- **Privacy-first** - optional fully-offline pipeline (local STT + Ollama). Audio can
  stay 100% on your machine.
- **BYOK + local** - bring your own API key, or run free with local models. No lock-in.
- **Honest by design** - built for live prep, accessibility (real-time captions for HoH
  users, ESL/neurodivergent support), and private meeting notes.

## Status

Working end to end on Linux/KDE, and still rough. The loop runs: system audio in, live
transcription, question detection, RAG over your CV and the job description, a streamed answer
in the overlay, and the conversation saved to history.

Around it: a setup wizard, a dashboard with a model manager (pull, delete, disk checks), an
editable system prompt and answer styles, conversation history with Markdown/JSON export, and
global shortcuts through the XDG portal. Local by default via whisper.cpp (Vulkan) and Ollama,
with BYOK Anthropic/OpenAI/Gemini and cloud STT as options.

Not yet: packaging beyond running from source, and anything but Linux has had no real testing.

## Choosing what gets captured

By default the engine records the **default sink's monitor**, which is all system audio: the
call, but also notification sounds, music, and every other tab.

**Settings > Audio > Capture from** narrows that to one application. The engine creates a
private null sink, routes that app's audio into it, loops it back to your speakers so you still
hear the call, and records only that sink. In testing, a second application playing at the same
time was attenuated by a factor of ~1.4e6, so it is isolation rather than a volume difference.

Two things worth knowing:

- An application's *output* is the far side of a call, so an isolated capture transcribes **the
  interviewer and not you**. That is usually what you want for question detection; your own
  answers not appearing in the transcript is expected, not a fault.
- The sink exists only while an interview is running and is released when you leave it, so your
  audio routing is never left changed behind your back.

The app does not have to be playing when you pick it. Anything that has played audio on the
machine is listed, and it is captured from the moment it starts.

## Architecture (short version)

A **Python/FastAPI engine** does capture, STT, RAG and LLM orchestration, and broadcasts
transcript + answer events over an **authenticated WebSocket** to any number of thin
clients. The **Tauri overlay** is the primary client; a phone/browser viewer can subscribe
to the same stream.

## Install

```bash
sudo apt install ./interviewr-overlay_0.1.0_amd64.deb
```

Built for Ubuntu 24.04 and newer. Other Ubuntu and Debian based distros should work but are untested.
The package includes its own Python and the GPU (Vulkan) transcription build, so nothing is compiled
on your machine. Settings go in `~/.config/interviewr/` and data in `~/.local/share/interviewr/`, and
both are kept if you uninstall.

Ollama isn't bundled. The setup wizard checks for it, shows the install command if it's missing, and
recommends a model that fits your GPU.

To build the package yourself, run `./scripts/build-deb.sh` (needs docker). It builds on Ubuntu 24.04
so the result also installs on newer releases.

## Running from a checkout

```bash
./scripts/install.sh       # engine + GPU transcription; re-run safely
cd overlay && npm install && npm run tauri dev
```

The installer creates `backend/.venv`, then builds `pywhispercpp` against Vulkan so
transcription runs on the GPU. That build cannot be done by `pip install` alone: pip serves a
CPU-only wheel and ignores the build flags, and the resulting libraries need an RPATH fix
before they will even import. `scripts/install.sh` handles both, prompts once for the system
build packages it needs, and refuses to report success on a silent CPU fallback. Pass `--cpu`
to skip the GPU path deliberately. See `docs/linux-vulkan-build.md` for what it automates and
why.

The setup wizard covers the rest on first run: it checks for Ollama and shows the install
command if it is missing, recommends an LLM that fits your GPU's VRAM and downloads it, and
fetches a whisper model. That is why there is no `ollama pull` step here, since naming a model
up front would ignore what the wizard knows about your hardware.

A checkout keeps its config and databases beside the source (`.env` at the repo root,
`backend/data/`). An installed copy, which has no writable source tree, uses
`~/.config/interviewr/` and `~/.local/share/interviewr/` instead; `INTERVIEWR_CONFIG_DIR` and
`INTERVIEWR_STATE_DIR` override either.

To run the engine on its own:

```bash
cd backend
.venv/bin/python run_pipeline.py        # or: .venv/bin/uvicorn app.main:app --reload
# -> http://127.0.0.1:8765/health
```

## Support this project

This is free and open source. If it helped you, consider supporting development:

- **GitHub Sponsors:** https://github.com/sponsors/PSYEONE
- **Crypto:** https://nowpayments.io/donation/psyeone (any of ~350 coins)

Found a problem or have an idea? [Open an issue](https://github.com/PSYEONE/interviewr-ai/issues/new).

Every bit keeps this Linux-first, privacy-first, and subscription-free.

## License

[AGPL-3.0](./LICENSE). Chosen so a competitor can't take this, close it, and resell it
as a SaaS without sharing their changes back.
