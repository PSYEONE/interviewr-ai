"""Blob accounting and models-directory resolution, on fixtures (no real Ollama install)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.system import disk
from app.system.disk import (
    blob_name,
    partial_bytes,
    partial_paths,
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


# A store owned by the ollama service, inside a folder the user isn't allowed into.

_ROOT_IGNORES_PERMISSIONS = pytest.mark.skipif(os.geteuid() == 0, reason="root can read any folder")


@pytest.fixture
def locked_store(tmp_path: Path) -> Iterator[Path]:
    """Mirrors /usr/share/ollama (mode 750, owned by ollama) as seen by a user outside that group."""
    home = tmp_path / "ollama"
    store = home / ".ollama" / "models"
    (store / "blobs").mkdir(parents=True)
    home.chmod(0)
    yield store
    home.chmod(0o755)  # so pytest can clean up tmp_path


@_ROOT_IGNORES_PERMISSIONS
def test_resolve_models_dir_finds_a_store_it_cannot_enter(
    locked_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    monkeypatch.setattr(disk, "_MODELS_DIR_CANDIDATES", (locked_store,))
    assert resolve_models_dir(None) == locked_store
    assert resolve_models_dir(locked_store) == locked_store


@_ROOT_IGNORES_PERMISSIONS
def test_probe_disk_reports_free_space_for_a_store_it_cannot_enter(locked_store: Path) -> None:
    info = probe_disk(locked_store)
    assert info is not None
    assert info.models_mb is None  # size unknown, not zero
    assert info.partial_mb == 0
    assert 0 < info.free_mb <= info.total_mb


# Interrupted downloads.


def _sparse(path: Path, apparent: int, written: int) -> None:
    """A file that claims `apparent` bytes while only committing `written`, as Ollama's
    preallocated download files do."""
    with path.open("wb") as fh:
        fh.truncate(apparent)
        fh.write(b"x" * written)


def test_partial_bytes_counts_allocated_not_apparent(tmp_path: Path) -> None:
    """The real case: a 40GB partial holding 21GB. getsize would overstate by 19GB."""
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    _sparse(blobs / "sha256-aaa-partial", apparent=64 * 1024 * 1024, written=4 * 1024 * 1024)

    counted = partial_bytes(tmp_path)
    assert counted < 8 * 1024 * 1024, "counted the hole, not the data"
    assert counted >= 4 * 1024 * 1024


def test_partial_paths_ignores_finished_blobs(tmp_path: Path) -> None:
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    (blobs / "sha256-aaa").write_bytes(b"complete")
    (blobs / "sha256-bbb-partial").write_bytes(b"in flight")
    (blobs / "sha256-bbb-partial-0").write_bytes(b"chunk state")

    names = {p.name for p in partial_paths(tmp_path)}
    assert names == {"sha256-bbb-partial", "sha256-bbb-partial-0"}


def test_partials_are_zero_when_the_store_is_clean(tmp_path: Path) -> None:
    (tmp_path / "blobs").mkdir()
    assert partial_bytes(tmp_path) == 0
    assert partial_paths(tmp_path) == []


def test_probe_disk_reports_partials_within_the_store(tmp_path: Path) -> None:
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    (blobs / "sha256-aaa").write_bytes(b"y" * (2 * 1024 * 1024))
    _sparse(blobs / "sha256-bbb-partial", apparent=32 * 1024 * 1024, written=3 * 1024 * 1024)

    disk = probe_disk(tmp_path)
    assert disk is not None
    # models_mb must also be allocated-based, or it would read ~34MB here instead of ~5MB.
    assert disk.models_mb is not None and disk.models_mb < 8
    assert 2 <= disk.partial_mb < 6
