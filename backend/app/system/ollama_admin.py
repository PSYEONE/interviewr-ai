"""Ollama lifecycle + model management for the setup flow.

Generation stays in ``llm/ollama.py``; this is the admin surface the onboarding wizard
drives: is Ollama installed and running, which models are local, and pull a new one with
streamed progress. We never silently install Ollama (running ``curl | sh`` as the user is a
privileged action); we surface the official command for the UI to run on explicit confirm.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncGenerator

import httpx
from pydantic import BaseModel

# Surfaced (not executed) when Ollama is absent; the UI runs it only on explicit user confirm.
INSTALL_COMMAND = "curl -fsSL https://ollama.com/install.sh | sh"


def normalize_tag(name: str) -> str:
    """Ollama reports a bare tag as `name:latest`; config may store it either way."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


class ModelInfo(BaseModel):
    name: str
    size: int
    parameter_size: str | None = None
    quantization: str | None = None
    family: str | None = None


class PullProgress(BaseModel):
    status: str
    completed: int | None = None
    total: int | None = None
    percent: float | None = None


class OllamaAdmin:
    """Thin async wrapper over Ollama's HTTP API for setup-time operations."""

    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # No read timeout: a model pull streams for minutes. Connect timeout still bounds a
        # dead daemon. `transport` is for tests (httpx.MockTransport).
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(None, connect=10.0),
            transport=transport,
        )

    @staticmethod
    def is_installed() -> bool:
        return shutil.which("ollama") is not None

    async def version(self) -> str | None:
        try:
            resp = await self._client.get("/api/version")
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        version = resp.json().get("version")
        return str(version) if version else None

    async def is_running(self) -> bool:
        return await self.version() is not None

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
        except httpx.HTTPError:
            return []
        models: list[ModelInfo] = []
        for entry in resp.json().get("models", []):
            details = entry.get("details") or {}
            models.append(
                ModelInfo(
                    name=entry["name"],
                    size=int(entry.get("size", 0)),
                    parameter_size=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                    family=details.get("family"),
                )
            )
        return models

    async def pull_model(self, model: str) -> AsyncGenerator[PullProgress, None]:
        """Stream `/api/pull` progress until the download finishes (status 'success')."""
        async with self._client.stream(
            "POST", "/api/pull", json={"model": model, "stream": True}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                completed = data.get("completed")
                total = data.get("total")
                percent = (
                    round(100.0 * completed / total, 1)
                    if isinstance(completed, int) and isinstance(total, int) and total > 0
                    else None
                )
                yield PullProgress(
                    status=str(data.get("status", "")),
                    completed=completed,
                    total=total,
                    percent=percent,
                )

    async def delete_model(self, model: str) -> bool:
        """Remove a pulled model. False when Ollama doesn't have it.

        Mutating, so transport errors propagate (same contract as pull_model). A 404 is a
        normal answer here, not a failure. Note httpx's .delete() takes no json= kwarg.
        """
        resp = await self._client.request("DELETE", "/api/delete", json={"model": model})
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    async def close(self) -> None:
        await self._client.aclose()
