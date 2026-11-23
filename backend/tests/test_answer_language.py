"""The answer prompt names the language the question was asked in, stated outright for small models."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from app.events import EventBus
from app.llm.base import Chunk, Message
from app.session import Session
from app.stt.languages import LANGUAGE_NAMES, language_name


class _FakeLLM:
    def __init__(self) -> None:
        self.last: list[Message] = []

    async def check_health(self) -> bool:
        return True

    async def warmup(self) -> None:
        return None

    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        self.last = list(messages)
        yield Chunk("ok")

    async def close(self) -> None:
        return None


async def _answer(session: Session) -> None:
    session.request_answer()
    assert session._gen_task is not None
    await session._gen_task


async def test_the_prompt_states_the_language_of_the_question() -> None:
    """gemma3:4b answered a Bulgarian transcript in English under the general instruction alone,
    because the retrieved resume chunks were English. Naming the language fixes that."""
    llm = _FakeLLM()
    session = Session(EventBus(), llm)
    session.add_transcript("Какъв е вашият опит с Python?", "bg")
    await _answer(session)
    prompt = llm.last[-1]["content"]
    assert prompt.startswith("Какъв е вашият опит с Python?")
    assert prompt.endswith("The question was asked in Bulgarian. Answer in Bulgarian.")
    # The hint is for this request only, never part of the stored conversation.
    assert "Bulgarian" not in session._history[0]["content"]


async def test_the_latest_utterance_decides_when_languages_mix() -> None:
    llm = _FakeLLM()
    session = Session(EventBus(), llm)
    session.add_transcript("Разкажете за себе си.", "bg")
    session.add_transcript("And what about Python?", "en")
    await _answer(session)
    assert llm.last[-1]["content"].endswith("Answer in English.")


async def test_no_language_known_means_no_hint() -> None:
    # A transcript added without a language, as before this change, keeps the old prompt shape.
    llm = _FakeLLM()
    session = Session(EventBus(), llm)
    session.add_transcript("Tell me about yourself.")
    await _answer(session)
    assert "was asked in" not in llm.last[-1]["content"]


def test_every_whisper_language_has_a_name() -> None:
    pytest.importorskip("pywhispercpp")
    from pywhispercpp.model import Model

    missing = [code for code in Model.available_languages() if code not in LANGUAGE_NAMES]
    assert missing == []
    assert language_name("bg") == "Bulgarian"
    assert language_name("xx") == "xx"
