"""Ollama registry tag parsing and manifest size lookup, against a mocked transport."""

from __future__ import annotations

import httpx

from app.system.registry import manifest_layers, parse_tag

_MANIFEST = {
    "config": {"digest": "sha256:cfg", "size": 500},
    "layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:aaa", "size": 4_000},
        {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:bbb", "size": 20},
    ],
}


def _transport(seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/v2/library/llama3.1/manifests/8b":
            return httpx.Response(200, json=_MANIFEST)
        # What the real registry returns for a name it doesn't have: 200 with an errors body.
        return httpx.Response(200, json={"errors": [{"code": "MANIFEST_UNKNOWN"}]})

    return httpx.MockTransport(handler)


def test_parse_tag_defaults_to_the_library_namespace_and_latest() -> None:
    assert parse_tag("phi4") == ("library/phi4", "latest")
    assert parse_tag("llama3.1:8b") == ("library/llama3.1", "8b")
    assert parse_tag("  qwen3:8b  ") == ("library/qwen3", "8b")


def test_parse_tag_keeps_an_explicit_namespace() -> None:
    assert parse_tag("someone/custom:v1") == ("someone/custom", "v1")
    assert parse_tag("someone/custom") == ("someone/custom", "latest")


def test_parse_tag_declines_other_registries() -> None:
    """A host in the first segment isn't ours to query, so the caller must fall open."""
    assert parse_tag("hf.co/user/model:q4") is None
    assert parse_tag("localhost:5000/model") is None
    assert parse_tag("") is None


async def test_manifest_layers_sums_config_and_layers() -> None:
    seen: list[httpx.Request] = []
    layers = await manifest_layers("llama3.1:8b", transport=_transport(seen))
    assert layers is not None
    assert sorted(layers) == [("sha256:aaa", 4_000), ("sha256:bbb", 20), ("sha256:cfg", 500)]
    assert seen[0].headers["accept"].startswith("application/vnd.docker")


async def test_manifest_layers_is_none_for_an_unknown_model() -> None:
    assert await manifest_layers("not-a-real-model", transport=_transport()) is None


async def test_manifest_layers_is_none_for_another_registry() -> None:
    assert await manifest_layers("hf.co/user/model", transport=_transport()) is None


async def test_manifest_layers_is_none_when_the_registry_is_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert await manifest_layers("phi4", transport=httpx.MockTransport(fail)) is None
