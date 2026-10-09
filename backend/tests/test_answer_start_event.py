"""answer.start carries the question it was generated from, so clients can pair a turn."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.events import AnswerStartEvent, EventBus, Subscription
from app.llm.base import Chunk, Message
from app.session import Session


class _FakeLLM:
    async def check_health(self) -> bool:
        return True

    async def warmup(self) -> None:
        return None

    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        yield Chunk("ok")

    async def close(self) -> None:
        return None


def _starts(sub: Subscription) -> list[AnswerStartEvent]:
    out: list[AnswerStartEvent] = []
    while True:
        try:
            ev = sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            return out
        if isinstance(ev, AnswerStartEvent):
            out.append(ev)


async def test_answer_start_carries_the_question() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    s = Session(bus, _FakeLLM())
    s.add_transcript("tell me about a hard bug you fixed")
    s.request_answer()
    assert s._gen_task is not None
    await s._gen_task

    events = _starts(sub)
    assert len(events) == 1
    assert events[0].question == "tell me about a hard bug you fixed"
    # source_chars stays consistent with it, so an existing client reading only the length is fine.
    assert events[0].source_chars == len(events[0].question)


async def test_question_is_the_buffer_at_request_time_not_later_speech() -> None:
    """Anything spoken while generating belongs to the next turn, not this one."""
    bus = EventBus()
    sub = bus.subscribe()
    s = Session(bus, _FakeLLM())
    s.add_transcript("first question")
    s.request_answer()
    s.add_transcript("something said while it was thinking")
    assert s._gen_task is not None
    await s._gen_task

    assert _starts(sub)[0].question == "first question"
