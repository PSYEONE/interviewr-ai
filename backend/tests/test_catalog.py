"""The generated catalog.json: shape, coverage, and (marked `network`) freshness upstream."""

from __future__ import annotations

import json
import re

import pytest

from app.system.hardware import _CATALOG_JSON, _CURATED, catalog


def test_catalog_json_covers_every_curated_model() -> None:
    raw = json.loads(_CATALOG_JSON.read_text())
    assert set(raw["models"]) == {m.name for m in _CURATED}


def test_generated_at_is_a_date() -> None:
    """Stamped so staleness is legible in the file rather than only in git history."""
    raw = json.loads(_CATALOG_JSON.read_text())
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw["generated_at"])


def test_every_entry_has_sane_facts() -> None:
    for m in catalog():
        assert m.download_mb is not None and m.download_mb > 0, m.name
        assert m.params is not None and re.fullmatch(r"[\d.]+[BM]", m.params), m.name
        assert m.quant, m.name
        assert m.family, m.name


def test_catalog_merges_curation_with_generated_facts() -> None:
    """The two halves are joined by tag: the name/label from Python, the numbers from JSON."""
    by_name = {m.name: m for m in catalog()}
    entry = by_name["llama3.1:8b"]
    assert entry.label == "Llama 3.1 8B"  # hand-written
    assert entry.params == "8.0B"  # generated; the old hand-typed value said "8B"
    assert entry.quant == "Q4_K_M"


def test_sizes_are_plausible_for_a_q4_build() -> None:
    """A Q4_K_M GGUF runs ~0.6 bytes/param, so size and param count must agree."""
    for m in catalog():
        assert m.params is not None and m.download_mb is not None
        params_b = float(m.params.rstrip("BM")) * (0.001 if m.params.endswith("M") else 1)
        bytes_per_param = (m.download_mb * 1024 * 1024) / (params_b * 1e9)
        assert 0.4 < bytes_per_param < 1.2, f"{m.name}: {bytes_per_param:.3f} B/param"


@pytest.mark.network
def test_catalog_json_is_current() -> None:
    """Fails when a tag is removed, renamed or re-quantised upstream.

    This is the staleness guard the old "Verified against ollama.com (June 2026)" comment only
    claimed. Excluded from the default run so the suite stays offline; the scheduled workflow
    runs `scripts/refresh_catalog.py --check`, which asserts the same thing.
    """
    import asyncio
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from refresh_catalog import build  # type: ignore[import-not-found]

    fresh = asyncio.run(build())
    current = json.loads(_CATALOG_JSON.read_text())
    assert current["models"] == fresh["models"], "run scripts/refresh_catalog.py"


# --- the refresh script's stamp handling -------------------------------------------------------


def _stamp_for() -> object:
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "scripts"))
    from refresh_catalog import stamp_for  # type: ignore[import-not-found]

    return stamp_for


def test_stamp_is_kept_when_the_facts_are_unchanged() -> None:
    """Otherwise the scheduled workflow opens a PR every day containing only a new date."""
    stamp_for = _stamp_for()
    models = {"phi4": {"download_mb": 8633}}
    current = {"generated_at": "2026-01-01", "models": models}
    assert stamp_for(models, current, "2026-08-24") == "2026-01-01"


def test_stamp_advances_when_a_model_changed() -> None:
    stamp_for = _stamp_for()
    current = {"generated_at": "2026-01-01", "models": {"phi4": {"download_mb": 8633}}}
    assert stamp_for({"phi4": {"download_mb": 9000}}, current, "2026-08-24") == "2026-08-24"


def test_stamp_advances_when_there_is_no_previous_file() -> None:
    stamp_for = _stamp_for()
    assert stamp_for({"phi4": {}}, {}, "2026-08-24") == "2026-08-24"
