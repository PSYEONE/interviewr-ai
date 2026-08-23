"""Ollama registry tag parsing and manifest size lookup, against a mocked transport."""

from __future__ import annotations

import httpx

from app.system.registry import manifest_layers, parse_hf_ref, parse_tag

# Hugging Face serves the same manifest shape as registry.ollama.ai; digests here are the blob
# names the daemon will use, which is why they compare directly against the local store.
_HF_MANIFEST = {
    "config": {"digest": "sha256:hfcfg", "size": 551},
    "layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:hfmod", "size": 807_694_464},
        {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:hftpl", "size": 1_481},
    ],
}

_HF_IQ3_M = {
    "config": {"digest": "sha256:hfcfg2", "size": 551},
    "layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:hfiq3", "size": 657_289_344},
    ],
}

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
        if request.url.path == "/v2/someone/gguf-repo/manifests/latest":
            return httpx.Response(200, json=_HF_MANIFEST)
        if request.url.path == "/v2/someone/gguf-repo/manifests/IQ3_M":
            return httpx.Response(200, json=_HF_IQ3_M)
        # HF's real replies: 400 for a quant it can't serve, 401 for a missing or private repo.
        if request.url.path == "/v2/someone/gguf-repo/manifests/Q2_K":
            return httpx.Response(400, json={"error": "not a valid quantization scheme"})
        if request.url.host == "huggingface.co":
            return httpx.Response(401, json={"error": "Invalid username or password."})
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


def test_parse_tag_declines_hosted_refs() -> None:
    """A host in the first segment isn't ollama.com's; hf.co is handled by parse_hf_ref instead."""
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


async def test_manifest_layers_is_none_for_an_unsupported_registry() -> None:
    """Neither ollama.com nor Hugging Face, so there is no metadata endpoint to ask."""
    assert await manifest_layers("localhost:5000/some/model", transport=_transport()) is None
    assert await manifest_layers("ghcr.io/user/model:v1", transport=_transport()) is None


async def test_manifest_layers_is_none_when_the_registry_is_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert await manifest_layers("phi4", transport=httpx.MockTransport(fail)) is None


# --- Hugging Face -----------------------------------------------------------------------------


def test_parse_hf_ref_accepts_both_hosts_and_defaults_to_latest() -> None:
    assert parse_hf_ref("hf.co/user/repo") == ("user/repo", "latest")
    assert parse_hf_ref("huggingface.co/user/repo") == ("user/repo", "latest")
    assert parse_hf_ref("hf.co/user/repo:Q8_0") == ("user/repo", "Q8_0")
    assert parse_hf_ref("HF.CO/user/repo:Q8_0") == ("user/repo", "Q8_0")


def test_parse_hf_ref_declines_anything_else() -> None:
    assert parse_hf_ref("llama3.1:8b") is None
    assert parse_hf_ref("hf.co/onlyone") is None  # needs user/repo
    assert parse_hf_ref("hf.co/a/b/c") is None
    assert parse_hf_ref("") is None


async def test_hf_untagged_resolves_latest_server_side() -> None:
    """The registry picks the default quant, so we never guess which file a bare repo means."""
    layers = await manifest_layers("hf.co/someone/gguf-repo", transport=_transport())
    assert layers is not None
    assert sum(size for _digest, size in layers) == 807_696_496


async def test_hf_quant_tag_selects_that_manifest() -> None:
    layers = await manifest_layers("hf.co/someone/gguf-repo:IQ3_M", transport=_transport())
    assert layers == [("sha256:hfiq3", 657_289_344), ("sha256:hfcfg2", 551)]


async def test_hf_digests_are_blob_names_so_dedup_works() -> None:
    """Verified against the live API: the model layer digest is the file's sha256 (HF lfs.oid)."""
    layers = await manifest_layers("hf.co/someone/gguf-repo", transport=_transport())
    assert layers is not None
    assert ("sha256:hfmod", 807_694_464) in layers


async def test_hf_both_host_spellings_work() -> None:
    for host in ("hf.co", "huggingface.co"):
        layers = await manifest_layers(f"{host}/someone/gguf-repo:IQ3_M", transport=_transport())
        assert layers is not None


async def test_hf_unservable_quant_falls_open() -> None:
    """HF 400s a quant it has no manifest for, including subfolder split quants."""
    assert await manifest_layers("hf.co/someone/gguf-repo:Q2_K", transport=_transport()) is None


async def test_hf_missing_or_private_repo_falls_open() -> None:
    """HF answers 401 for both, so a typo and a private repo are indistinguishable."""
    assert await manifest_layers("hf.co/someone/private", transport=_transport()) is None
