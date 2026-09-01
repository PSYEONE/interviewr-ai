"""Disk capacity around Ollama's model store, for the pull pre-check and the UI's usage bar.

Best-effort like the rest of ``system/``: a missing or unreadable models directory degrades to
``None`` rather than raising, and callers must treat ``None`` as "unknown, don't block anything".
Arithmetic lives in pure argument-taking helpers so it is testable without a real filesystem.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import BaseModel

_MB = 1024 * 1024

# Where Ollama keeps blobs when OLLAMA_MODELS isn't set. The user install writes under $HOME; the
# Linux package runs as its own service user, so the systemd path is a real and common case.
_MODELS_DIR_CANDIDATES = (
    Path("/usr/share/ollama/.ollama/models"),
    Path("/var/lib/ollama/.ollama/models"),
)


class DiskInfo(BaseModel):
    path: str  # the directory the figures were measured against
    total_mb: int
    free_mb: int
    models_mb: int | None  # size of Ollama's model store; None when it isn't readable
    partial_mb: int  # of that, bytes held by interrupted downloads (see partial_bytes)


def blob_name(digest: str) -> str:
    """Registry digest (`sha256:abc`) to its on-disk blob filename (`sha256-abc`)."""
    return digest.replace(":", "-", 1)


def missing_bytes(layers: list[tuple[str, int]], present: set[str]) -> int:
    """Bytes a pull would actually fetch, skipping layers already in the blob store.

    Tags routinely share blobs (a `:latest` and its version tag are usually the same manifest),
    so charging the full manifest size would refuse pulls that cost nothing.
    """
    return sum(size for digest, size in layers if blob_name(digest) not in present)


def present_blobs(models_dir: Path) -> set[str]:
    """Blob filenames already in the store. Empty set when the directory can't be listed."""
    try:
        return {entry.name for entry in (models_dir / "blobs").iterdir()}
    except OSError:
        return set()


def resolve_models_dir(override: Path | None) -> Path | None:
    """The configured models directory, else the first default location that exists."""
    if override is not None:
        return override if override.is_dir() else None
    # Computed per call, not at import, so the home path can't be baked in before tests set it.
    for candidate in (Path.home() / ".ollama" / "models", *_MODELS_DIR_CANDIDATES):
        if candidate.is_dir():
            return candidate
    return None


def _allocated(path: str) -> int:
    """Blocks actually committed to disk, which is not the file's length.

    Ollama preallocates a download's full length as a sparse file, so an interrupted 40 GB pull
    reports 40 GB via getsize while occupying only what it managed to fetch.
    """
    return int(os.stat(path).st_blocks) * 512


def _dir_size_bytes(path: Path) -> int | None:
    try:
        return sum(
            _allocated(os.path.join(root, name))
            for root, _dirs, files in os.walk(path)
            for name in files
        )
    except OSError:
        return None


def partial_paths(models_dir: Path) -> list[Path]:
    """Files left behind by interrupted downloads.

    Ollama never removes these: it globs them to resume a later pull, and deletes them only on a
    successful completion. Nothing references them from a manifest, so `ollama list` cannot show
    them and `ollama rm` cannot reclaim them.
    """
    try:
        return sorted((models_dir / "blobs").glob("*-partial*"))
    except OSError:
        return []


def partial_bytes(models_dir: Path) -> int:
    """Disk actually held by interrupted downloads."""
    total = 0
    for path in partial_paths(models_dir):
        try:
            total += _allocated(str(path))
        except OSError:
            continue
    return total


def probe_disk(override: Path | None) -> DiskInfo | None:
    """Capacity of the filesystem holding Ollama's models. None when the store can't be found."""
    models_dir = resolve_models_dir(override)
    if models_dir is None:
        return None
    try:
        usage = shutil.disk_usage(models_dir)
    except OSError:
        return None
    models_bytes = _dir_size_bytes(models_dir)
    return DiskInfo(
        path=str(models_dir),
        total_mb=usage.total // _MB,
        free_mb=usage.free // _MB,
        models_mb=None if models_bytes is None else models_bytes // _MB,
        partial_mb=partial_bytes(models_dir) // _MB,
    )
