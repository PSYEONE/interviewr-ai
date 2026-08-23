"""Blob accounting and models-directory resolution, on fixtures (no real Ollama install)."""

from __future__ import annotations

from pathlib import Path

from app.system.disk import (
    blob_name,
    missing_bytes,
    present_blobs,
    probe_disk,
    resolve_models_dir,
)

# (digest, size) as the registry manifest reports them.
_LAYERS = [
    ("sha256:aaa", 4_000_000_000),  # the weights
    ("sha256:bbb", 1_500),  # template
    ("sha256:ccc", 500),  # params
]


def test_blob_name_swaps_only_the_first_colon() -> None:
    assert blob_name("sha256:abc123") == "sha256-abc123"


def test_missing_bytes_skips_blobs_already_on_disk() -> None:
    assert missing_bytes(_LAYERS, set()) == 4_000_002_000  # nothing local: full download
    assert missing_bytes(_LAYERS, {"sha256-aaa"}) == 2_000  # weights shared with another tag
    assert missing_bytes(_LAYERS, {"sha256-aaa", "sha256-bbb", "sha256-ccc"}) == 0


def test_missing_bytes_is_zero_for_a_fully_shared_tag() -> None:
    """`:latest` and its version tag are usually one manifest, so re-pulling costs nothing."""
    present = {blob_name(d) for d, _ in _LAYERS}
    assert missing_bytes(_LAYERS, present) == 0


def test_present_blobs_reads_the_blob_directory(tmp_path: Path) -> None:
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    (blobs / "sha256-aaa").write_bytes(b"x")
    (blobs / "sha256-bbb").write_bytes(b"y")
    assert present_blobs(tmp_path) == {"sha256-aaa", "sha256-bbb"}


def test_present_blobs_is_empty_when_unreadable(tmp_path: Path) -> None:
    assert present_blobs(tmp_path / "nope") == set()


def test_resolve_models_dir_prefers_an_existing_override(tmp_path: Path) -> None:
    assert resolve_models_dir(tmp_path) == tmp_path


def test_resolve_models_dir_rejects_a_missing_override(tmp_path: Path) -> None:
    """A configured path that isn't there is a config error, not a reason to guess elsewhere."""
    assert resolve_models_dir(tmp_path / "absent") is None


def test_probe_disk_reports_megabytes_and_store_size(tmp_path: Path) -> None:
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    (blobs / "sha256-aaa").write_bytes(b"x" * (3 * 1024 * 1024))
    disk = probe_disk(tmp_path)
    assert disk is not None
    assert disk.path == str(tmp_path)
    assert disk.models_mb == 3
    assert disk.total_mb > 0
    assert 0 <= disk.free_mb <= disk.total_mb


def test_probe_disk_is_none_when_the_store_is_missing(tmp_path: Path) -> None:
    assert probe_disk(tmp_path / "absent") is None
