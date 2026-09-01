"""Model download sizes, read before a pull starts.

A pull's own progress stream reports `total` per layer and never announces the whole download up
front, so the only way to know what a model costs before fetching it is its manifest. Hugging Face
serves the same Ollama-native manifest as registry.ollama.ai, so `hf.co/...` tags resolve through
the identical path, quant selection included.

Everything here fails open: an unknown tag, a bad quant, a private repo or an unreachable network
returns None, and the caller must let the pull proceed rather than blocking on a failed lookup.
"""

from __future__ import annotations

from typing import Literal

import httpx

_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"

# Both hosts serve the same Ollama-native manifest shape, so one fetch/parse covers both.
_REGISTRY = "https://registry.ollama.ai"
_HF_REGISTRY = "https://huggingface.co"
_HF_HOSTS = ("hf.co/", "huggingface.co/")

# "missing" means a registry positively denied the tag; "unknown" means we could not find
# out, and the caller must fall open rather than block a pull that would have worked.
Resolution = Literal["found", "missing", "unknown"]


def parse_tag(tag: str) -> tuple[str, str] | None:
    """Split an Ollama tag into (repository, version), or None for another registry.

    `phi4` -> ("library/phi4", "latest"); `llama3.1:8b` -> ("library/llama3.1", "8b");
    `user/model:v1` -> ("user/model", "v1"). A leading host (`hf.co/...`) isn't ours to query.
    """
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
    """Split a Hugging Face tag into (repo, quant), or None if it isn't one.

    `hf.co/user/repo` -> ("user/repo", "latest"); `hf.co/user/repo:Q8_0` -> ("user/repo", "Q8_0").
    Both `hf.co` and `huggingface.co` are valid hosts. Resolving what "latest" means (Q4_K_M when
    present, otherwise a reasonable quant in the repo) is the registry's job, not ours.
    """
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
    """Parse an Ollama-native manifest. The layer digests are the blob names the daemon will use,
    so they compare directly against what is already in the local store."""
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
    """Whether `tag` exists upstream, and its blobs when it does.

    Only a registry that positively denies the tag yields "missing": ollama.com answers 404 with
    MANIFEST_UNKNOWN for an unknown name or a typo'd tag, and Hugging Face 400s a quant it cannot
    serve. A 401 stays "unknown", because HF answers that for a private repo as well as a typo and
    Ollama can pull private repos with its own key even though we cannot see them.
    """
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
