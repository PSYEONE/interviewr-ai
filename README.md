# interviewr-ai

**The interview/meeting copilot that runs on Linux and keeps your data on your machine.**

interviewr-ai listens to your interviews and meetings, transcribes them in real time, and
surfaces AI-generated answers in a native overlay. It is **Linux-first**,
**privacy-first**, and **subscription-free**.

- **Linux-first** - native PipeWire/Wayland audio capture.
- **Privacy-first** - optional fully-offline pipeline (local STT + Ollama). Audio can
  stay 100% on your machine.
- **BYOK + local** - bring your own API key, or run free with local models.

## Status

Working end to end on Linux/KDE, and still rough. The loop runs: system audio in, live
transcription, question detection, RAG over your CV and the job description, a streamed answer
in the overlay, and the conversation saved to history.

Around it: a setup wizard, a dashboard with a model manager (pull, delete, disk checks), an
editable system prompt and answer styles, conversation history with Markdown/JSON export, and
global shortcuts through the XDG portal. Local by default via whisper.cpp (Vulkan) and Ollama,
with BYOK Anthropic/OpenAI/Gemini and cloud STT as options.

## Choosing what gets captured

By default the engine records the **default sink's monitor**, which is all system audio: the
call, but also notification sounds, music, and every other tab.

**Settings > Audio > Capture from** narrows that to one application. The engine creates a
private null sink, routes that app's audio into it, loops it back to your speakers so you still
hear the call, and records only that sink. In testing, a second application playing at the same
time was attenuated by a factor of ~1.4e6, so it is isolation rather than a volume difference.

## Architecture

A **Python/FastAPI engine** does capture, STT, RAG and LLM orchestration, and broadcasts
transcript + answer events over an **authenticated WebSocket** to any number of thin
clients. The **Tauri overlay** is the primary client; a phone/browser viewer can subscribe
to the same stream.

## Install

Download the `.deb` from the [latest release](https://github.com/PSYEONE/interviewr-ai/releases/latest),
then:

```bash
sudo apt install ./interviewr-ai_0.1.0_amd64.deb
```

Built for Ubuntu 24.04 and newer. Other Ubuntu and Debian based distros should work but are untested.
The package includes its own Python and the GPU (Vulkan) transcription build, so nothing is compiled
on your machine.

To build the package yourself, run `./scripts/build-deb.sh` (needs docker). It builds on Ubuntu 24.04
so the result also installs on newer releases.

## Running from a checkout

```bash
./scripts/install.sh       # engine + GPU transcription; re-run safely
cd overlay && npm install && npm run tauri dev
```

The installer creates `backend/.venv` and builds `pywhispercpp` against Vulkan so transcription
runs on the GPU. A plain `pip install` cannot do that, since pip serves a CPU-only wheel. The
script prompts once for the build packages it needs and refuses to report success if the GPU
build quietly fell back to the CPU. Pass `--cpu` to skip the GPU path deliberately, and see
`docs/linux-vulkan-build.md` for what it automates and why.

To run the engine on its own:

```bash
cd backend
.venv/bin/python run_pipeline.py        # or: .venv/bin/uvicorn app.main:app --reload
# -> http://127.0.0.1:8765/health
```

## First run

Ollama isn't bundled. The setup wizard checks for it and shows the install command if it is
missing, recommends an LLM that fits your GPU's VRAM and downloads it, and fetches a whisper
model. That is why there is no `ollama pull` step above: naming a model up front would ignore
what the wizard knows about your hardware.

## Where your data lives

A checkout keeps its config and databases beside the source (`.env` at the repo root,
`backend/data/`). An installed copy, which has no writable source tree, uses
`~/.config/interviewr/` and `~/.local/share/interviewr/` instead, and keeps both if you
uninstall. `INTERVIEWR_CONFIG_DIR` and `INTERVIEWR_STATE_DIR` override either.

## Support this project

This is free and open source. If it helped you, consider supporting development:

- **GitHub Sponsors:** https://github.com/sponsors/PSYEONE
- **Crypto:** https://nowpayments.io/donation/psyeone (any of ~350 coins)

Found a problem or have an idea? [Open an issue](https://github.com/PSYEONE/interviewr-ai/issues/new).

Every bit keeps this Linux-first, privacy-first, and subscription-free.

## License

[AGPL-3.0](./LICENSE).
