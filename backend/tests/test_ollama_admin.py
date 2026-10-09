"""OllamaAdmin against a mocked HTTP transport, using the documented API JSON shapes."""

from __future__ import annotations

import json
import shutil

import httpx
import pytest

from app.system.ollama_admin import OllamaAdmin, normalize_tag

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


def _transport(seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.13.0"})
        if path == "/api/tags":
            return httpx.Response(200, json=_TAGS)
        if path == "/api/pull":
            return httpx.Response(200, content=_PULL_NDJSON)
        if path == "/api/delete":
            # Ollama 404s a tag it doesn't have; anything else here is a real success.
            known = json.loads(request.content)["model"] == "llama3.1:8b"
            return httpx.Response(200 if known else 404)
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


async def test_a_refused_pull_is_reported_rather_than_ending_quietly() -> None:
    # Ollama answers 200 and then says why it refused, so raise_for_status never fires.
    refused = (
        b'{"status":"pulling manifest"}\n'
        b'{"error":"pull model manifest: 412: \\nThe model you are attempting to pull '
        b'requires a newer version of Ollama.\\n"}\n'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=refused)

    admin = OllamaAdmin("http://ollama", transport=httpx.MockTransport(handler))
    try:
        events = [p async for p in admin.pull_model("gemma3:4b")]
    finally:
        await admin.close()
    assert events[-1].status == "error"
    assert events[-1].error is not None
    assert "newer version of Ollama" in events[-1].error
    assert all(e.status != "success" for e in events)


async def test_delete_model_sends_delete_with_body() -> None:
    seen: list[httpx.Request] = []
    admin = OllamaAdmin("http://ollama", transport=_transport(seen))
    try:
        assert await admin.delete_model("llama3.1:8b") is True
    finally:
        await admin.close()
    assert seen[-1].method == "DELETE"
    assert json.loads(seen[-1].content) == {"model": "llama3.1:8b"}


async def test_delete_missing_model_is_false_not_an_error() -> None:
    admin = OllamaAdmin("http://ollama", transport=_transport())
    try:
        assert await admin.delete_model("never-pulled:1b") is False
    finally:
        await admin.close()


async def test_unreachable_daemon_is_soft() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    admin = OllamaAdmin("http://ollama", transport=httpx.MockTransport(fail))
    try:
        assert await admin.version() is None
        assert await admin.list_models() == []
        # Mutations stay hard: the caller must see that the delete never happened.
        with pytest.raises(httpx.HTTPError):
            await admin.delete_model("llama3.1:8b")
    finally:
        await admin.close()


def test_normalize_tag_strips_only_latest() -> None:
    assert normalize_tag("llama3.1:latest") == "llama3.1"
    assert normalize_tag("llama3.1:8b") == "llama3.1:8b"
    assert normalize_tag("llama3.1") == "llama3.1"


def test_is_installed_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/ollama")
    assert OllamaAdmin.is_installed() is True
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert OllamaAdmin.is_installed() is False
