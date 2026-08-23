"""Model download sizes from the Ollama registry, read before a pull starts.

A pull's own progress stream reports `total` per layer and never announces the whole download up
front, so the only way to know what a model costs before fetching it is the manifest. Everything
here fails open: an unknown tag, a third-party registry or an unreachable network returns None, and
the caller must let the pull proceed rather than blocking on a failed lookup.
"""

from __future__ import annotations

import httpx

_REGISTRY = "https://registry.ollama.ai"
_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"


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


async def manifest_layers(
    tag: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[tuple[str, int]] | None:
    """(digest, size) for every blob a pull of `tag` would need, or None if it can't be known."""
    parsed = parse_tag(tag)
    if parsed is None:
        return None
    repo, version = parsed
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            resp = await client.get(
                f"{_REGISTRY}/v2/{repo}/manifests/{version}", headers={"Accept": _ACCEPT}
            )
            resp.raise_for_status()
            body = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    layers = body.get("layers")
    if not isinstance(layers, list):
        return None  # e.g. {"errors": [{"code": "MANIFEST_UNKNOWN"}]}
    out = [(str(entry["digest"]), int(entry["size"])) for entry in layers if "digest" in entry]
    config = body.get("config")
    if isinstance(config, dict) and "digest" in config:
        out.append((str(config["digest"]), int(config.get("size", 0))))
    return out
