"""OllamaAdmin against a mocked HTTP transport, using the documented API JSON shapes."""

from __future__ import annotations

import shutil

import httpx
import pytest

from app.system.ollama_admin import OllamaAdmin

_TAGS = {
    "models": [
        {
            "name": "llama3.1:8b",
            "size": 4920753328,
            "details": {
                "parameter_size": "8.0B",
                "quantization_level": "Q4_K_M",
                "family": "llama",
            },
        }
    ]
}

_PULL_NDJSON = (
    b'{"status":"pulling manifest"}\n'
    b'{"status":"pulling abc","digest":"abc","total":100,"completed":50}\n'
    b'{"status":"success"}\n'
)


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.13.0"})
        if path == "/api/tags":
            return httpx.Response(200, json=_TAGS)
        if path == "/api/pull":
            return httpx.Response(200, content=_PULL_NDJSON)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_version_and_running() -> None:
    admin = OllamaAdmin("http://ollama", transport=_transport())
    try:
        assert await admin.version() == "0.13.0"
        assert await admin.is_running() is True
    finally:
        await admin.close()


async def test_list_models_parses_details() -> None:
    admin = OllamaAdmin("http://ollama", transport=_transport())
    try:
        models = await admin.list_models()
    finally:
        await admin.close()
    assert len(models) == 1
    assert models[0].name == "llama3.1:8b"
    assert models[0].parameter_size == "8.0B"
    assert models[0].quantization == "Q4_K_M"


async def test_pull_model_streams_progress() -> None:
    admin = OllamaAdmin("http://ollama", transport=_transport())
    try:
        events = [p async for p in admin.pull_model("llama3.1:8b")]
    finally:
        await admin.close()
    assert events[0].status == "pulling manifest"
    assert events[1].percent == 50.0
    assert events[-1].status == "success"


async def test_unreachable_daemon_is_soft() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    admin = OllamaAdmin("http://ollama", transport=httpx.MockTransport(fail))
    try:
        assert await admin.version() is None
        assert await admin.list_models() == []
    finally:
        await admin.close()


def test_is_installed_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/ollama")
    assert OllamaAdmin.is_installed() is True
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert OllamaAdmin.is_installed() is False
