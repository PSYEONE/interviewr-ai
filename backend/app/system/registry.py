"""Looks up a model's download size from its registry manifest before pulling; a failed lookup never blocks a pull."""

from __future__ import annotations

from typing import Literal

import httpx

_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"

# Both hosts serve the same Ollama-native manifest shape, so one fetch/parse covers both.
_REGISTRY = "https://registry.ollama.ai"
_HF_REGISTRY = "https://huggingface.co"
_HF_HOSTS = ("hf.co/", "huggingface.co/")

# "missing" = the registry says the tag doesn't exist; "unknown" = we couldn't find out, so allow the pull.
Resolution = Literal["found", "missing", "unknown"]


def parse_tag(tag: str) -> tuple[str, str] | None:
    """Splits an Ollama tag into (repository, version), e.g. `llama3.1:8b` -> ("library/llama3.1", "8b"); None for other registries."""
    name = tag.strip()
    if not name:
        return None
    parts = name.split("/")
    # A dot or a port in the first segment means an explicit registry host, not a namespace.
    if len(parts) > 2 or (len(parts) == 2 and ("." in parts[0] or ":" in parts[0])):
        return None
    repo, _, version = name.rpartition(":")
    if not repo:  # no ':' at all, so rpartition put everything in `version`
        repo, version = version, "latest"
    if "/" not in repo:
        repo = f"library/{repo}"
    return repo, version


def parse_hf_ref(tag: str) -> tuple[str, str] | None:
    """Splits a Hugging Face tag into (repo, quant), e.g. `hf.co/user/repo:Q8_0` -> ("user/repo", "Q8_0"); None otherwise."""
    name = tag.strip()
    for host in _HF_HOSTS:
        if name.lower().startswith(host):
            rest = name[len(host) :]
            break
    else:
        return None
    repo, _, quant = rest.rpartition(":")
    if not repo:  # no ':' at all, so rpartition put everything in `quant`
        repo, quant = quant, ""
    return (repo, quant or "latest") if repo.count("/") == 1 and all(repo.split("/")) else None


async def _manifest_layers(
    base: str, repo: str, version: str, client: httpx.AsyncClient
) -> list[tuple[str, int]] | None:
    """Parses an Ollama-style manifest into (blob digest, size) pairs that match local blob names."""
    resp = await client.get(f"{base}/v2/{repo}/manifests/{version}", headers={"Accept": _ACCEPT})
    resp.raise_for_status()
    body = resp.json()
    layers = body.get("layers")
    if not isinstance(layers, list):
        return None  # an error body: MANIFEST_UNKNOWN, a bad quant, or a private repo
    out = [(str(entry["digest"]), int(entry["size"])) for entry in layers if "digest" in entry]
    config = body.get("config")
    if isinstance(config, dict) and "digest" in config:
        out.append((str(config["digest"]), int(config.get("size", 0))))
    return out


async def resolve_model(
    tag: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[Resolution, list[tuple[str, int]] | None]:
    """Whether `tag` exists upstream and its blobs if so; only a 400 or 404 counts as missing (a 401 may be a private repo)."""
    hf = parse_hf_ref(tag)
    parsed = (_HF_REGISTRY, *hf) if hf is not None else None
    if parsed is None:
        ollama = parse_tag(tag)
        parsed = (_REGISTRY, *ollama) if ollama is not None else None
    if parsed is None:
        return "unknown", None
    base, repo, version = parsed
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            layers = await _manifest_layers(base, repo, version, client)
    except httpx.HTTPStatusError as exc:
        return ("missing" if exc.response.status_code in (400, 404) else "unknown"), None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return "unknown", None
    # A 200 carrying an error body instead of layers is also a positive denial.
    return ("found", layers) if layers else ("missing", None)


async def manifest_layers(
    tag: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[tuple[str, int]] | None:
    """(digest, size) for every blob a pull of `tag` would need, or None if it can't be known."""
    return (await resolve_model(tag, transport=transport))[1]
