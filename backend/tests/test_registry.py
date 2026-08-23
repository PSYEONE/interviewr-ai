"""Ollama registry tag parsing and manifest size lookup, against a mocked transport."""

from __future__ import annotations

import httpx

from app.system.registry import manifest_layers, parse_hf_ref, parse_tag

# A GGUF repo shaped like the real ones: flat quants at the root, and the biggest quant split
# across parts inside its own subfolder (which a non-recursive listing would miss entirely).
_HF_TREE = [
    {"type": "file", "path": ".gitattributes", "size": 1_500},
    {"path": "Model-Q4_K_M.gguf", "size": 807_694_464, "lfs": {"oid": "aaa", "size": 807_694_464}},
    {"path": "Model-IQ3_M.gguf", "size": 657_000_000, "lfs": {"oid": "bbb", "size": 657_000_000}},
    {"path": "Model-Q8_0.gguf", "size": 1_321_000_000},  # no lfs block: fall back to `size`
    {
        "path": "Model-Q5_K_L/Model-Q5_K_L-00001-of-00002.gguf",
        "size": 39_850_000_000,
        "lfs": {"oid": "ccc", "size": 39_850_000_000},
    },
    {
        "path": "Model-Q5_K_L/Model-Q5_K_L-00002-of-00002.gguf",
        "size": 10_740_000_000,
        "lfs": {"oid": "ddd", "size": 10_740_000_000},
    },
]

# A repo whose quants exist but include no Q4_K_M, so an untagged pull has no documented default.
_HF_NO_DEFAULT = [{"path": "Other-Q6_K.gguf", "size": 5_000, "lfs": {"oid": "eee", "size": 5_000}}]

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
        if request.url.path == "/api/models/someone/gguf-repo/tree/main":
            return httpx.Response(200, json=_HF_TREE)
        if request.url.path == "/api/models/someone/no-default/tree/main":
            return httpx.Response(200, json=_HF_NO_DEFAULT)
        if request.url.path.startswith("/api/models/"):
            return httpx.Response(404, json={"error": "Repo not found"})
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


def test_parse_hf_ref_accepts_both_hosts_and_an_optional_quant() -> None:
    assert parse_hf_ref("hf.co/user/repo") == ("user/repo", None)
    assert parse_hf_ref("huggingface.co/user/repo") == ("user/repo", None)
    assert parse_hf_ref("hf.co/user/repo:Q8_0") == ("user/repo", "Q8_0")
    assert parse_hf_ref("HF.CO/user/repo:Q8_0") == ("user/repo", "Q8_0")


def test_parse_hf_ref_declines_anything_else() -> None:
    assert parse_hf_ref("llama3.1:8b") is None
    assert parse_hf_ref("hf.co/onlyone") is None  # needs user/repo
    assert parse_hf_ref("hf.co/a/b/c") is None
    assert parse_hf_ref("") is None


async def test_hf_untagged_uses_the_documented_q4_k_m_default() -> None:
    layers = await manifest_layers("hf.co/someone/gguf-repo", transport=_transport())
    assert layers == [("sha256:aaa", 807_694_464)]


async def test_hf_quant_tag_is_case_insensitive() -> None:
    for tag in ("hf.co/someone/gguf-repo:IQ3_M", "hf.co/someone/gguf-repo:iq3_m"):
        assert await manifest_layers(tag, transport=_transport()) == [("sha256:bbb", 657_000_000)]


async def test_hf_falls_back_to_plain_size_without_an_lfs_block() -> None:
    layers = await manifest_layers("hf.co/someone/gguf-repo:Q8_0", transport=_transport())
    assert layers == [("hf:Model-Q8_0.gguf", 1_321_000_000)]


async def test_hf_full_filename_works_as_a_tag() -> None:
    layers = await manifest_layers(
        "hf.co/someone/gguf-repo:Model-IQ3_M.gguf", transport=_transport()
    )
    assert layers == [("sha256:bbb", 657_000_000)]


async def test_hf_sums_every_part_of_a_split_quant() -> None:
    """The parts live in a subfolder; missing them would under-count a 50 GB pull by 10 GB."""
    layers = await manifest_layers("hf.co/someone/gguf-repo:Q5_K_L", transport=_transport())
    assert layers is not None
    assert len(layers) == 2
    assert sum(size for _digest, size in layers) == 50_590_000_000


async def test_hf_unknown_quant_falls_open() -> None:
    assert await manifest_layers("hf.co/someone/gguf-repo:Q2_K", transport=_transport()) is None


async def test_hf_untagged_repo_without_the_default_quant_falls_open() -> None:
    """Ollama picks 'a reasonable quant' here; guessing it wrong would refuse over a made-up size."""
    assert await manifest_layers("hf.co/someone/no-default", transport=_transport()) is None


async def test_hf_missing_or_private_repo_falls_open() -> None:
    assert await manifest_layers("hf.co/someone/private", transport=_transport()) is None
