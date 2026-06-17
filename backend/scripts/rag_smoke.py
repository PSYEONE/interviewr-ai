"""Live RAG smoke test against a real Ollama embedder. Run after `ollama pull nomic-embed-text`.

    .venv/bin/python scripts/rag_smoke.py

Ingests a tiny resume + JD, asks two questions, and prints the retrieved chunks so the
embed API shape and end-to-end retrieval can be eyeballed. Not part of the unit suite.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.config import get_settings
from app.rag import build_retriever

_RESUME = """Alex Rivera - Computer Systems Engineer.
Built a Linux-first, privacy-first interview copilot: PipeWire audio capture, whisper.cpp
Vulkan STT, FastAPI WebSocket engine, Tauri overlay.
Strong Python, systemd, PostgreSQL. Runs Kubuntu on Wayland with an AMD RX 7900 XT.
"""

_JD = """Senior Backend Engineer (Realtime / AI).
We need someone fluent in Python and async services, comfortable with audio/ML pipelines
and GPU acceleration. Bonus: Rust, Linux audio (PipeWire), local LLM inference.
"""


async def main() -> None:
    settings = get_settings()
    retriever = build_retriever(settings)
    assert retriever is not None, "RAG is disabled in config"
    if not await retriever.check_health():
        raise SystemExit("embedder unhealthy: is Ollama up and nomic-embed-text pulled?")

    with tempfile.TemporaryDirectory() as d:
        resume = Path(d) / "resume.txt"
        jd = Path(d) / "jd.txt"
        resume.write_text(_RESUME)
        jd.write_text(_JD)
        # Fresh start so reruns are deterministic.
        retriever.clear()
        r1 = await retriever.ingest_file("resume.txt", "resume", resume.read_bytes())
        r2 = await retriever.ingest_file("jd.txt", "job_description", jd.read_bytes())
        print(f"ingested resume={r1['chunk_count']} chunks, jd={r2['chunk_count']} chunks")
        print(f"model_id={retriever.store.model_id()}\n")

        for question in (
            "What GPU and OS does the candidate use?",
            "Does this role want Rust and audio pipeline experience?",
        ):
            print(f"Q: {question}")
            for hit in await retriever.retrieve(question):
                snippet = hit.text.replace("\n", " ")[:90]
                print(f"  [{hit.kind} {hit.score:.3f}] {snippet}")
            print()

    await retriever.close()


if __name__ == "__main__":
    asyncio.run(main())
