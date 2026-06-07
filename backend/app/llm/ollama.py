"""Ollama streaming LLM client."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

SYSTEM_PROMPT = (
    "You are a real-time meeting and interview copilot. "
    "You receive live audio transcription and provide concise, helpful responses. "
    "Focus on answering questions detected in the transcript, "
    "providing relevant context, and surfacing key points. "
    "Be direct and brief."
)


class OllamaStreamer:
    def __init__(self, base_url: str, model: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._model = model
        self._transcript_parts: list[str] = []
        self._max_chars = 2000

    async def check_health(self) -> bool:
        try:
            resp = await self._client.get("/api/version")
            return resp.status_code == 200
        except httpx.ConnectError:
            return False

    async def stream_response(self, new_transcript: str) -> AsyncIterator[str]:
        """Send accumulated transcript to Ollama and yield streamed tokens."""
        self._transcript_parts.append(new_transcript)
        transcript = " ".join(self._transcript_parts)

        # trim to last N chars
        if len(transcript) > self._max_chars:
            transcript = transcript[-self._max_chars :]
            self._transcript_parts = [transcript]

        async with self._client.stream(
            "POST",
            "/api/chat",
            json={
                "model": self._model,
                "stream": True,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Live transcript:\n{transcript}\n\n"
                            "Provide a helpful, concise response."
                        ),
                    },
                ],
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                token = data.get("message", {}).get("content", "")
                if token:
                    yield token
                if data.get("done", False):
                    break

    async def close(self) -> None:
        await self._client.aclose()
