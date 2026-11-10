"""Issue #12: the language is passed on every decode, detected once per utterance, and pinned."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.stt.whisper import WhisperTranscriber, transcribe_stream

RATE = 16000
CHUNK_S = 0.1


class _FakeModel:
    """Stands in for pywhispercpp's Model: records every decode's params and every detection."""

    def __init__(self, detect: str = "bg", prob: float = 0.94) -> None:
        self.detect = detect
        self.prob = prob
        self.detections = 0
        self.calls: list[dict[str, Any]] = []

    def auto_detect_language(self, _audio: np.ndarray) -> Any:
        self.detections += 1
        return (self.detect, np.float32(self.prob)), {}

    def transcribe(self, _audio: np.ndarray, **params: Any) -> list[Any]:
        self.calls.append(params)
        # Before the language is pinned whisper.cpp guesses for itself, often wrongly on a short
        # buffer (Greek for Bulgarian), so an undecided ("") decode comes back in the wrong script.
        text = "αυτό μία δύο τρία" if params["language"] == "" else "една дума още тук"
        return [SimpleNamespace(text=text)]


class _Stub(WhisperTranscriber):
    """The real transcriber logic over a fake model, so no whisper.cpp is needed."""

    def __init__(self, model: _FakeModel, language: str = "auto", english_only: bool = False) -> None:
        self.on_gpu = True
        self.language = language
        self.english_only = english_only
        self._lock = threading.Lock()
        self._model = model  # type: ignore[assignment]


def _audio(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def test_language_and_translate_are_always_passed() -> None:
    """The bug behind #12: pywhispercpp sets only what it is given and whisper.cpp defaults to
    "en", so a multilingual model silently translated Bulgarian until both were passed."""
    model = _FakeModel()
    _Stub(model, language="auto").transcribe_raw(_audio(1))
    _Stub(model, language="bg").transcribe_raw(_audio(1))
    _Stub(model).transcribe_raw(_audio(1), "de")
    # "" is how whisper.cpp is asked to detect; "auto" would also work but logs an error line.
    assert [c["language"] for c in model.calls] == ["", "bg", "de"]
    assert all(c["translate"] is False for c in model.calls)


def test_an_english_only_model_ignores_the_language_without_failing() -> None:
    model = _FakeModel()
    stub = _Stub(model, language="bg", english_only=True)
    assert stub.resolve_language(_audio(1)) == "en"
    stub.transcribe_raw(_audio(1), "bg")
    assert model.calls[0]["language"] == "en"
    assert model.detections == 0


def test_detection_is_logged_so_a_reporter_can_paste_it(caplog: pytest.LogCaptureFixture) -> None:
    model = _FakeModel(detect="bg", prob=0.94)
    with caplog.at_level(logging.INFO, logger="app.stt.whisper"):
        assert _Stub(model).resolve_language(_audio(1)) == "bg"
    assert "detected language: bg (0.94)" in caplog.text


def test_a_fixed_language_never_detects() -> None:
    model = _FakeModel()
    assert _Stub(model, language="de").resolve_language(_audio(1)) == "de"
    assert model.detections == 0


async def _utterances(
    count: int, on_start: list[Any] | None = None, speech_s: float = 4.0
) -> AsyncIterator[np.ndarray]:
    """``count`` utterances of ``speech_s`` speech each, separated by enough silence to end them."""
    loud = np.full(int(CHUNK_S * RATE), 0.5, dtype=np.float32)
    quiet = np.zeros(int(CHUNK_S * RATE), dtype=np.float32)
    for i in range(count):
        if on_start and i < len(on_start):
            on_start[i]()
        for _ in range(int(speech_s / CHUNK_S)):
            yield loud
        for _ in range(10):
            yield quiet


async def _run(stream: AsyncIterator[np.ndarray], stub: _Stub, **kwargs: Any) -> list[str]:
    finals: list[str] = []
    async for hyp in transcribe_stream(stream, stub, sample_rate=RATE, **kwargs):
        if hyp.final and hyp.text:
            finals.append(hyp.text)
    return finals


async def test_hypotheses_carry_the_pinned_language_for_the_answer_prompt() -> None:
    """The session tells the LLM which language the question was in, so every committed word
    must say which language it was decoded in; before the pin there is nothing to say."""
    model = _FakeModel(detect="bg")
    seen: list[tuple[bool, str]] = []
    async for hyp in transcribe_stream(_utterances(1), _Stub(model), sample_rate=RATE):
        seen.append((hyp.final, hyp.language))
    assert all(lang == "bg" for final, lang in seen if final)
    assert seen[0] == (False, "")  # a pre-pin partial
    assert any(lang == "bg" for final, lang in seen if not final)  # partials after the pin


async def test_the_language_is_detected_once_per_utterance_and_pinned() -> None:
    """Detecting on every partial flips languages mid-sentence; one detection per utterance, reused
    for its remaining partials and its final pass, is what keeps a sentence in one language."""
    model = _FakeModel(detect="bg")
    finals = await _run(_utterances(2), _Stub(model))
    assert finals  # the stream still commits text
    assert model.detections == 2  # exactly one per utterance
    languages = [c["language"] for c in model.calls]
    assert languages.count("bg") > 2  # partials plus finals under the pinned code
    # Each utterance: undecided ("") partials on the short buffer, then bg once pinned, no flips.
    runs = [lang for i, lang in enumerate(languages) if i == 0 or lang != languages[i - 1]]
    assert runs == ["", "bg", "", "bg"]


async def test_nothing_is_committed_before_the_language_is_pinned() -> None:
    """On 1 s of Bulgarian, large-v3-turbo detected Greek. Committing those early words would
    leave a Greek-script fragment at the start of every Bulgarian sentence."""
    model = _FakeModel(detect="bg")
    finals = await _run(_utterances(1), _Stub(model))
    assert finals
    assert not any("α" in text or "δύο" in text for text in finals)


async def test_a_fixed_language_pins_at_once_without_waiting() -> None:
    model = _FakeModel()
    await _run(_utterances(1), _Stub(model, language="de"))
    assert model.detections == 0
    assert {c["language"] for c in model.calls} == {"de"}


async def test_a_reload_applies_from_the_next_utterance() -> None:
    model = _FakeModel(detect="bg")
    stub = _Stub(model, language="auto")
    configured = {"language": "auto"}

    def switch_to_english() -> None:
        configured["language"] = "en"

    await _run(
        _utterances(2, on_start=[lambda: None, switch_to_english]),
        stub,
        language=lambda: configured["language"],
    )
    assert model.detections == 1  # only the first utterance needed detection
    languages = [c["language"] for c in model.calls]
    first_en = languages.index("en")
    assert "bg" in languages[:first_en]
    assert set(languages[first_en:]) == {"en"}  # the second utterance never guessed again


async def test_partials_off_still_detects_on_the_whole_utterance() -> None:
    # CPU mode: no partials, so the only detection sees the full utterance, which is more accurate.
    model = _FakeModel(detect="bg")
    await _run(_utterances(1), _Stub(model), emit_partials=False, partial_interval_s=1e9)
    assert model.detections == 1
    assert [c["language"] for c in model.calls] == ["bg"]


async def test_a_short_utterance_is_still_detected_at_its_end() -> None:
    # Under DETECT_MIN_S of speech: no partial can pin, so the final pass detects on all of it.
    model = _FakeModel(detect="bg")
    finals = await _run(_utterances(1, speech_s=2.0), _Stub(model))
    assert finals
    assert model.detections == 1
    assert model.calls[-1]["language"] == "bg"
