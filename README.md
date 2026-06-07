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

Early development. Phase 0: prove the pipeline (capture > transcribe > stream answer).

## Architecture (short version)

A **Python/FastAPI engine** does capture, STT, RAG and LLM orchestration, and broadcasts
transcript + answer events over an **authenticated WebSocket** to any number of thin
clients. The **Tauri overlay** is the primary client; a phone/browser viewer can subscribe
to the same stream.

## Quick start (backend)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
GGML_VULKAN=1 pip install -e ".[local,cloud,dev]"
cp ../.env.example ../.env   # fill in INTERVIEWR_LLM_MODEL etc.
ollama pull qwen3:8b          # or your preferred model

# run the Phase 0 pipeline
python run_pipeline.py

# or start the API server
uvicorn app.main:app --reload
# -> http://127.0.0.1:8765/health
```

## Support this project

This is free and open source. If it helped you, consider supporting development:

- **Ko-fi:** https://ko-fi.com/YOUR_USERNAME
- **GitHub Sponsors:** https://github.com/sponsors/PSYEONE
- **Crypto (self-hosted, no fees):** https://YOUR_BTCPAY_DOMAIN/apps/donate
  - **BTC:** `bc1...your-address`
  - **BTC Lightning:** via the BTCPay link above
  - **ETH / ERC-20:** `yourname.eth`  (or `0x...your-address`)

Every bit keeps this Linux-first, privacy-first, and subscription-free.

## License

[AGPL-3.0](./LICENSE). Chosen so a competitor can't take this, close it, and resell it
as a SaaS without sharing their changes back.
