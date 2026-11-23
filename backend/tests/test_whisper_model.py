"""Speech model choice per hardware, and the download that keeps a first run from hanging."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.stt import whisper as stt
from app.system.hardware import GpuInfo
from app.system.whisper_models import suggest_whisper, whisper_choice

_ENGLISH = ["tiny.en", "base.en", "small.en", "medium.en"]
_MULTILINGUAL = ["small", "medium", "large-v3-turbo", "large-v3-turbo-q5_0"]


def _gpu(vram_mb: int | None, kind: str = "discrete") -> GpuInfo:
    return GpuInfo(index=0, name="card", vendor="nvidia", kind=kind, vram_mb=vram_mb)


def test_no_gpu_gets_the_small_download() -> None:
    # small.en is barely more accurate than base.en, three times the download and twice as slow.
    assert suggest_whisper(None) == "base.en"


def test_more_vram_gets_a_more_accurate_model() -> None:
    assert suggest_whisper(4096) == "small.en"
    assert suggest_whisper(8192) == "medium.en"


def test_a_language_other_than_english_gets_a_multilingual_model() -> None:
    """Issue #12: a .en model turns Bulgarian into unrelated English, so the suggestion must
    follow the language. small drifts into Russian on Cyrillic, so a card with room gets turbo."""
    assert suggest_whisper(None, "bg") == "small"
    assert suggest_whisper(2048, "bg") == "large-v3-turbo-q5_0"  # needs about what small does
    assert suggest_whisper(4096, "bg") == "large-v3-turbo-q5_0"
    assert suggest_whisper(8192, "bg") == "large-v3-turbo"
    # English and auto-detect keep the measured .en ladder; auto alone is no sign of another language.
    assert suggest_whisper(4096, "en") == "small.en"
    assert suggest_whisper(4096, "auto") == "small.en"


def test_every_model_stays_selectable_with_a_fit_label() -> None:
    choice = whisper_choice([_gpu(8192)])
    assert choice.suggested == "medium.en"
    assert [m.name for m in choice.models] == _ENGLISH + _MULTILINGUAL
    assert {m.fit for m in choice.models} <= {"fits", "tight", "too_big"}


def test_english_only_models_are_marked_so_the_ui_can_say_so() -> None:
    by_name = {m.name: m for m in whisper_choice([_gpu(8192)]).models}
    assert all(by_name[n].english_only for n in _ENGLISH)
    assert not any(by_name[n].english_only for n in _MULTILINGUAL)
    # The measured accuracy is LibriSpeech English and must not be shown for other languages.
    assert all(by_name[n].live_wer is None and by_name[n].speed is None for n in _MULTILINGUAL)


def test_the_language_list_comes_from_whisper_itself() -> None:
    pytest.importorskip("pywhispercpp")
    languages = whisper_choice([_gpu(8192)]).languages
    assert "en" in languages and "bg" in languages
    assert len(languages) >= 99  # whisper's full set, so the UI never hardcodes it


def test_the_suggestion_follows_the_language_being_chosen() -> None:
    assert whisper_choice([_gpu(8192)], language="bg").suggested == "large-v3-turbo"


def test_gpu_transcription_off_is_treated_as_cpu() -> None:
    choice = whisper_choice([_gpu(8192)], use_gpu=False)
    assert choice.suggested == "base.en"
    assert all(m.fit == "cpu" for m in choice.models)


def test_an_integrated_gpu_does_not_count_as_vram() -> None:
    assert whisper_choice([_gpu(2048, kind="integrated")]).suggested == "base.en"


@pytest.fixture
def models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(stt, "model_path", lambda name: tmp_path / f"ggml-{name}.bin")
    yield tmp_path


def test_ensure_model_keeps_a_model_already_on_disk(models_dir: Path) -> None:
    existing = models_dir / "ggml-base.en.bin"
    existing.write_bytes(b"weights")

    assert stt.ensure_model("base.en") == existing
    assert existing.read_bytes() == b"weights"


def _fake_stream(body: bytes, *, fail: bool = False) -> Any:
    class _Response:
        headers = {"content-length": str(len(body))}

        def raise_for_status(self) -> None:
            if fail:
                raise httpx.HTTPStatusError("404", request=None, response=None)  # type: ignore[arg-type]

        def iter_bytes(self, _size: int) -> Iterator[bytes]:
            yield body

    class _Stream:
        def __enter__(self) -> _Response:
            return _Response()

        def __exit__(self, *_: object) -> None:
            return None

    return lambda *_args, **_kwargs: _Stream()


def test_ensure_model_downloads_and_reports_progress(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(httpx, "stream", _fake_stream(b"x" * 1024))
    seen: list[int] = []

    path = stt.ensure_model("base.en", seen.append)

    assert path.read_bytes() == b"x" * 1024
    assert seen[-1] == 100
    # The part file is renamed, so a half-finished download is never mistaken for a model.
    assert list(models_dir.glob("*.part")) == []


def test_ensure_model_reports_a_failed_download_clearly(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "stream", boom)

    with pytest.raises(RuntimeError, match="could not download speech model 'base.en'"):
        stt.ensure_model("base.en")
    assert list(models_dir.iterdir()) == []
