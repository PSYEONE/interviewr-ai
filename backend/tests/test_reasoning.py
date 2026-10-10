"""Reasoning is off unless asked for, streams separately from the answer, and never becomes history."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest

from app.events import AnswerReasoningEvent, AnswerTokenEvent, EventBus, Subscription
from app.llm.base import Chunk, Message
from app.llm.ollama import OllamaClient
from app.session import Session

_CHAT_NDJSON = (
    b'{"message":{"role":"assistant","thinking":"the budget is 1.5s, so "}}\n'
    b'{"message":{"role":"assistant","thinking":"segmentation has to be short"}}\n'
    b'{"message":{"role":"assistant","content":"Split it "}}\n'
    b'{"message":{"role":"assistant","content":"into three stages."},"done":true}\n'
)


def _client(seen: list[httpx.Request]) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_CHAT_NDJSON)

    return OllamaClient("http://ollama", "qwen3:8b", transport=httpx.MockTransport(handler))


async def test_thinking_is_switched_off_unless_asked_for() -> None:
    # Left unset, a hybrid model reasons by default and takes tens of seconds to say anything.
    seen: list[httpx.Request] = []
    client = _client(seen)
    try:
        [c async for c in client.generate([{"role": "user", "content": "hi"}])]
    finally:
        await client.close()
    assert json.loads(seen[0].content)["think"] is False


async def test_thinking_is_requested_when_the_user_turns_it_on() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    try:
        [c async for c in client.generate([{"role": "user", "content": "hi"}], reasoning=True)]
    finally:
        await client.close()
    assert json.loads(seen[0].content)["think"] is True


async def test_warmup_never_reasons() -> None:
    # A throwaway "ok" would otherwise cost a full reasoning pass at startup.
    seen: list[httpx.Request] = []
    client = _client(seen)
    try:
        await client.warmup()
    finally:
        await client.close()
    assert json.loads(seen[0].content)["think"] is False


async def test_closing_tells_ollama_to_drop_the_model() -> None:
    """Ollama is a separate service and keeps the weights for its whole keep-alive window, so
    quitting the app would otherwise leave several GB on the card for half an hour."""
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.close()
    body = json.loads(seen[-1].content)
    assert body["keep_alive"] == 0
    assert body["messages"] == []  # an unload, not a generation


async def test_an_unreachable_ollama_does_not_break_shutdown() -> None:
    def refused(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = OllamaClient("http://ollama", "qwen3:8b", transport=httpx.MockTransport(refused))
    await client.close()  # must not raise


async def test_reasoning_and_answer_arrive_as_separate_chunks() -> None:
    client = _client([])
    try:
        chunks = [c async for c in client.generate([{"role": "user", "content": "hi"}])]
    finally:
        await client.close()
    assert [c.text for c in chunks if c.reasoning] == [
        "the budget is 1.5s, so ",
        "segmentation has to be short",
    ]
    assert "".join(c.text for c in chunks if not c.reasoning) == "Split it into three stages."


class _ReasoningLLM:
    """Reasons first, then answers, and records what each call asked for."""

    def __init__(self) -> None:
        self.asked: list[bool] = []

    async def check_health(self) -> bool:
        return True

    async def warmup(self) -> None:
        return None

    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        self.asked.append(reasoning)
        if reasoning:
            yield Chunk("let me think about this", reasoning=True)
        yield Chunk("The answer.")

    async def close(self) -> None:
        return None


def _drain(sub: Subscription) -> list[Any]:
    out: list[Any] = []
    while True:
        try:
            out.append(sub.queue.get_nowait())
        except asyncio.QueueEmpty:
            return out


async def _answer(session: Session, question: str) -> None:
    session.add_transcript(question)
    session.request_answer()
    assert session._gen_task is not None
    await session._gen_task


async def test_the_session_does_not_reason_by_default() -> None:
    llm = _ReasoningLLM()
    session = Session(EventBus(), llm)
    await _answer(session, "tell me about a hard bug")
    assert llm.asked == [False]


async def test_reasoning_is_published_but_kept_out_of_the_answer() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    llm = _ReasoningLLM()
    session = Session(bus, llm)
    session.set_reasoning(True)
    await _answer(session, "design a captioning pipeline")

    events = _drain(sub)
    thoughts = [e.token for e in events if isinstance(e, AnswerReasoningEvent)]
    answer = "".join(e.token for e in events if isinstance(e, AnswerTokenEvent))
    assert thoughts == ["let me think about this"]
    assert answer == "The answer."
    # The stored turn feeds the next prompt, so a stray thought there would poison the context.
    assert session._history[-1]["content"] == "The answer."


class _CountingLLM(_ReasoningLLM):
    """Records warmups, and can block in one to prove they do not stack."""

    def __init__(self, hold: asyncio.Event | None = None, fail: bool = False) -> None:
        super().__init__()
        self.warmups = 0
        self._hold = hold
        self._fail = fail

    async def warmup(self) -> None:
        self.warmups += 1
        if self._fail:
            raise RuntimeError("ollama is down")
        if self._hold is not None:
            await self._hold.wait()


async def test_entering_an_interview_warms_the_model() -> None:
    # An evicted 8B costs seconds to reload; paid while the user is still talking it is invisible.
    llm = _CountingLLM()
    session = Session(EventBus(), llm)
    session.resume_capture()
    await asyncio.sleep(0)
    assert llm.warmups == 1
    await session.aclose()


async def test_warmups_do_not_stack_on_repeated_entries() -> None:
    hold = asyncio.Event()
    llm = _CountingLLM(hold)
    session = Session(EventBus(), llm)
    session.resume_capture()
    await asyncio.sleep(0)
    session.resume_capture()  # straight back in while the first is still running
    await asyncio.sleep(0)
    assert llm.warmups == 1
    hold.set()
    await session.aclose()


async def test_a_failed_warmup_is_logged_and_ignored(caplog: pytest.LogCaptureFixture) -> None:
    llm = _CountingLLM(fail=True)
    session = Session(EventBus(), llm)
    session.resume_capture()
    assert session._warm_task is not None
    await session._warm_task  # must not raise: transcription carries on without the LLM
    assert "warmup failed" in caplog.text
    await session.aclose()


async def test_a_title_never_reasons_even_with_reasoning_on() -> None:
    # Reasoning here would cost a minute and the first line could be a stray thought.
    llm = _ReasoningLLM()
    session = Session(EventBus(), llm)
    session.set_reasoning(True)
    title = await session._generate_title([{"id": 1, "asked_at": "2026-10-09T00:00:00", "question": "q", "answer": "a"}])
    assert llm.asked == [False]
    assert title == "The answer."
