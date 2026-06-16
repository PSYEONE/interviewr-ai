"""Test-set acquisition and manifest building.

Two sources: the LibriSpeech test sets (auto-downloaded, calibrated baseline) and a flat
folder of ``<stem>.<audio>`` + ``<stem>.txt`` pairs for real domain clips dropped in later.
"""

from __future__ import annotations

import random
import subprocess
import tarfile
from pathlib import Path
from typing import NamedTuple

_OPENSLR = "https://www.openslr.org/resources/12"
_AUDIO_EXTS = (".flac", ".wav", ".mp3", ".m4a", ".ogg", ".opus")


class Sample(NamedTuple):
    id: str
    audio_path: Path
    reference: str


def fetch_librispeech(subset: str, cache_dir: Path) -> Path:
    """Download and extract a LibriSpeech subset (e.g. ``test-clean``); return its dir.

    Skips the download/extract if already present. The tarball extracts to
    ``<cache_dir>/LibriSpeech/<subset>``.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    subset_dir = cache_dir / "LibriSpeech" / subset
    if subset_dir.is_dir():
        return subset_dir

    tar_path = cache_dir / f"{subset}.tar.gz"
    if not tar_path.is_file():
        url = f"{_OPENSLR}/{subset}.tar.gz"
        print(f"Downloading {url} ({subset}, a few hundred MB)...")
        subprocess.run(["curl", "-L", "--fail", "-o", str(tar_path), url], check=True)

    print(f"Extracting {tar_path.name}...")
    with tarfile.open(tar_path) as tar:
        tar.extractall(cache_dir, filter="data")
    return subset_dir


def load_librispeech(root: Path, n: int, seed: int) -> list[Sample]:
    """Build a sample list from an extracted LibriSpeech subset dir.

    Transcripts live in ``<spk>-<chapter>.trans.txt`` (``<utt-id> UPPERCASE TEXT`` per line)
    alongside the matching ``<utt-id>.flac``.
    """
    samples: list[Sample] = []
    for trans in root.rglob("*.trans.txt"):
        for line in trans.read_text(encoding="utf-8").splitlines():
            utt_id, _, text = line.partition(" ")
            audio = trans.parent / f"{utt_id}.flac"
            if text and audio.is_file():
                samples.append(Sample(utt_id, audio, text))
    return _sample(samples, n, seed)


def load_folder(directory: Path, n: int, seed: int) -> list[Sample]:
    """Build a sample list from a flat folder of ``<stem>.<audio>`` + ``<stem>.txt`` pairs."""
    samples: list[Sample] = []
    for audio in sorted(directory.iterdir()):
        if audio.suffix.lower() not in _AUDIO_EXTS:
            continue
        txt = audio.with_suffix(".txt")
        if txt.is_file():
            samples.append(Sample(audio.stem, audio, txt.read_text(encoding="utf-8").strip()))
    return _sample(samples, n, seed)


def _sample(samples: list[Sample], n: int, seed: int) -> list[Sample]:
    random.Random(seed).shuffle(samples)
    return samples[:n] if 0 < n < len(samples) else samples
