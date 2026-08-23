"""Model download sizes, read before a pull starts.

A pull's own progress stream reports `total` per layer and never announces the whole download up
front, so the only way to know what a model costs before fetching it is the source's own metadata:
the manifest on registry.ollama.ai, or the file listing on Hugging Face for `hf.co/...` tags.
Everything here fails open: an unknown tag, a private repo or an unreachable network returns None,
and the caller must let the pull proceed rather than blocking on a failed lookup.
"""

from __future__ import annotations

import re

import httpx

_REGISTRY = "https://registry.ollama.ai"
_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"

_HF_API = "https://huggingface.co/api/models"
_HF_HOSTS = ("hf.co/", "huggingface.co/")
_HF_DEFAULT_QUANT = "Q4_K_M"  # what Ollama picks when the tag omits one, per the HF docs


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


def parse_hf_ref(tag: str) -> tuple[str, str | None] | None:
    """Split a Hugging Face tag into (repo, quant), or None if it isn't one.

    `hf.co/user/repo` -> ("user/repo", None); `hf.co/user/repo:Q8_0` -> ("user/repo", "Q8_0").
    Both `hf.co` and `huggingface.co` are valid hosts. The quant may also be a full `.gguf`
    filename, which `_pick_files` handles.
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
    return (repo, quant or None) if repo.count("/") == 1 and all(repo.split("/")) else None


def _pick_files(paths: list[str], quant: str | None) -> list[str]:
    """The GGUF files a pull of this quant would fetch, matched on basename.

    A quant can span several files: big models ship split as `-Q5_K_L-00001-of-00002.gguf` inside
    a per-quant subfolder, so every part has to be summed or the estimate lands wildly short.
    """
    ggufs = [p for p in paths if p.lower().endswith(".gguf")]
    if quant and quant.lower().endswith(".gguf"):  # the full-filename tag form
        want = quant.lower()
        return [p for p in ggufs if p.rsplit("/", 1)[-1].lower() == want]
    pattern = re.compile(rf"-{re.escape((quant or _HF_DEFAULT_QUANT).lower())}(?:-\d+-of-\d+)?$")
    return [p for p in ggufs if pattern.search(p.rsplit("/", 1)[-1].lower()[: -len(".gguf")])]


async def _hf_layers(
    repo: str, quant: str | None, client: httpx.AsyncClient
) -> list[tuple[str, int]] | None:
    """Sizes of the GGUF files behind an `hf.co/...` tag, or None when they can't be resolved."""
    # recursive: the largest quants live in subfolders, and a flat listing misses them entirely.
    resp = await client.get(f"{_HF_API}/{repo}/tree/main", params={"recursive": "true"})
    resp.raise_for_status()
    entries = resp.json()
    if not isinstance(entries, list):
        return None
    sizes = {str(e["path"]): e for e in entries if isinstance(e, dict) and "path" in e}
    picked = _pick_files(list(sizes), quant)
    if not picked:
        # No match. When the tag named a quant it simply isn't there; when it didn't, Ollama falls
        # back to "one reasonable quant present in the repo", a heuristic we won't guess at.
        return None
    out: list[tuple[str, int]] = []
    for path in picked:
        entry = sizes[path]
        lfs = entry.get("lfs") or {}
        # lfs.oid is the file's sha256, which is how Ollama names blobs, so an already-imported
        # model can dedup. Unverified against a real import; a miss only over-charges the estimate.
        oid = lfs.get("oid")
        digest = f"sha256:{oid}" if oid else f"hf:{path}"
        out.append((digest, int(lfs.get("size") or entry.get("size") or 0)))
    return out


async def _ollama_layers(
    repo: str, version: str, client: httpx.AsyncClient
) -> list[tuple[str, int]] | None:
    url = f"{_REGISTRY}/v2/{repo}/manifests/{version}"
    resp = await client.get(url, headers={"Accept": _ACCEPT})
    resp.raise_for_status()
    body = resp.json()
    layers = body.get("layers")
    if not isinstance(layers, list):
        return None  # e.g. {"errors": [{"code": "MANIFEST_UNKNOWN"}]}
    out = [(str(entry["digest"]), int(entry["size"])) for entry in layers if "digest" in entry]
    config = body.get("config")
    if isinstance(config, dict) and "digest" in config:
        out.append((str(config["digest"]), int(config.get("size", 0))))
    return out


async def manifest_layers(
    tag: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[tuple[str, int]] | None:
    """(digest, size) for every blob a pull of `tag` would need, or None if it can't be known."""
    hf = parse_hf_ref(tag)
    ollama = parse_tag(tag) if hf is None else None
    if hf is None and ollama is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            if hf is not None:
                return await _hf_layers(hf[0], hf[1], client)
            if ollama is not None:
                return await _ollama_layers(ollama[0], ollama[1], client)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    return None  # unreachable: the guard above proves one of the two parses succeeded
