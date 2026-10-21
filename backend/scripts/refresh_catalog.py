#!/usr/bin/env python3
"""Regenerates app/system/catalog.json (download size, parameters, quantisation, family) from the Ollama registry.

The list of offered models lives in hardware.py. Use --check to fail without writing if the committed file is out of date,
and --strict to refuse a result that dropped a model or moved a size by more than a re-quantisation's worth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.system.hardware import _CATALOG_JSON, _CURATED  # noqa: E402
from app.system.registry import _ACCEPT, _REGISTRY, parse_tag  # noqa: E402

_MB = 1024 * 1024


async def _facts(tag: str, client: httpx.AsyncClient) -> dict[str, object]:
    """Size and model metadata for one tag, from its manifest plus config blob."""
    parsed = parse_tag(tag)
    if parsed is None:
        raise ValueError(f"{tag}: not an ollama.com tag")
    repo, version = parsed
    resp = await client.get(f"{_REGISTRY}/v2/{repo}/manifests/{version}", headers={"Accept": _ACCEPT})
    resp.raise_for_status()
    manifest = resp.json()
    if "layers" not in manifest:
        raise ValueError(f"{tag}: {manifest}")

    total = int(manifest["config"]["size"]) + sum(int(x["size"]) for x in manifest["layers"])
    blob = await client.get(f"{_REGISTRY}/v2/{repo}/blobs/{manifest['config']['digest']}")
    blob.raise_for_status()
    cfg = blob.json()
    return {
        "download_mb": total // _MB,
        "params": cfg.get("model_type"),
        "quant": cfg.get("file_type"),
        "family": cfg.get("model_family"),
    }


# A size change bigger than this is a re-quantisation, not drift, and changes which GPUs fit.
MAX_SIZE_CHANGE = 0.15


async def build() -> tuple[dict[str, object], list[str]]:
    """The fresh catalog plus the tags that did not resolve."""
    models: dict[str, object] = {}
    failed: list[str] = []
    # Blob reads 307 to R2 object storage; manifests don't, but one client serves both.
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for m in _CURATED:
            try:
                models[m.name] = await _facts(m.name, client)
                print(f"  ok    {m.name}")
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                failed.append(f"{m.name}: {exc}")
                print(f"  FAIL  {m.name}: {exc}", file=sys.stderr)
    if failed:
        # Loud, and the entry is simply absent: a half-known catalog is better than a wrong one.
        print(f"\n{len(failed)} tag(s) did not resolve; they will have no size.", file=sys.stderr)
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "models": dict(sorted(models.items())),
    }, failed


def guard(
    fresh_models: dict[str, object],
    current_models: dict[str, object],
    failed: list[str],
    max_change: float = MAX_SIZE_CHANGE,
) -> list[str]:
    """Reasons an unattended run must not write: tags that vanished, and sizes that jumped."""
    reasons = [f"{f} (tag did not resolve; replace it in _CURATED if it was pulled)" for f in failed]
    for name, facts in fresh_models.items():
        before = current_models.get(name)
        if not isinstance(before, dict) or not isinstance(facts, dict):
            continue
        old, new = before.get("download_mb"), facts.get("download_mb")
        if not isinstance(old, int) or not isinstance(new, int) or old <= 0:
            continue
        change = abs(new - old) / old
        if change > max_change:
            reasons.append(f"{name}: download size {old} -> {new} MB ({change:.0%}); check for a re-quantisation")
    return reasons


def _serialize(data: dict[str, object]) -> str:
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def stamp_for(fresh_models: object, current: dict[str, object], today: str) -> str:
    """Today's date, or the previous date if nothing changed, so the scheduled workflow doesn't open date-only PRs."""
    previous = current.get("generated_at")
    if current.get("models") == fresh_models and isinstance(previous, str) and previous:
        return previous
    return today


def _current() -> dict[str, object]:
    try:
        loaded = json.loads(_CATALOG_JSON.read_text())
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    ap.add_argument("--strict", action="store_true", help="refuse to write if a tag vanished or a size jumped")
    args = ap.parse_args()

    fresh, failed = asyncio.run(build())
    current = _current()
    if args.strict:
        current_models = current.get("models")
        reasons = guard(fresh["models"], current_models if isinstance(current_models, dict) else {}, failed)  # type: ignore[arg-type]
        if reasons:
            print("\nrefusing to write catalog.json:", file=sys.stderr)
            for r in reasons:
                print(f"  {r}", file=sys.stderr)
            return 1
    if args.check:
        if not current:
            print("catalog.json missing or unreadable", file=sys.stderr)
            return 1
        # Compare only the facts; the stamp is not part of what "up to date" means.
        if current.get("models") != fresh["models"]:
            print("catalog.json is out of date; run scripts/refresh_catalog.py", file=sys.stderr)
            return 1
        print("catalog.json is up to date")
        return 0

    fresh["generated_at"] = stamp_for(fresh["models"], current, str(fresh["generated_at"]))
    _CATALOG_JSON.write_text(_serialize(fresh))
    print(f"\nwrote {_CATALOG_JSON} ({len(fresh['models'])} models)")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
