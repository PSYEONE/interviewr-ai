"""Ollama setup and model management: install and running checks, listing, pulling and deleting models."""

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
    # Ollama reports a failed pull as a line on a 200 response, not an HTTP error.
    error: str | None = None


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

    async def capabilities(self, model: str) -> list[str]:
        """What a pulled model can do, e.g. 'thinking' or 'tools'. Empty when Ollama can't say."""
        try:
            resp = await self._client.post("/api/show", json={"model": normalize_tag(model)})
            resp.raise_for_status()
        except httpx.HTTPError:
            return []
        caps = resp.json().get("capabilities")
        return [str(c) for c in caps] if isinstance(caps, list) else []

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
                if data.get("error"):
                    yield PullProgress(status="error", error=str(data["error"]).strip())
                    return
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
        """Removes a downloaded model; False if Ollama doesn't have it, and other errors are raised."""
        # .request is used because httpx's .delete() can't send a JSON body.
        resp = await self._client.request("DELETE", "/api/delete", json={"model": model})
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    async def close(self) -> None:
        await self._client.aclose()
