"""Finalizing a conversation always labels it, even when LLM titling never completes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from app.events import EventBus
from app.history import HistoryStore, TurnRecord
from app.llm.base import Chunk, Message
from app.session import _fallback_title, Session


class _SlowLLM:
    """Never finishes generating, standing in for a timeout or a task cancelled by shutdown."""

    async def check_health(self) -> bool:
        return True

    async def warmup(self) -> None:
        return None

    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        await asyncio.sleep(3600)
        yield Chunk("never")

    async def close(self) -> None:
        return None


class _TitlingLLM(_SlowLLM):
    async def generate(
        self, messages: Sequence[Message], *, reasoning: bool = False
    ) -> AsyncIterator[Chunk]:
        yield Chunk("A Proper Generated Title")


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.sqlite3")


def _turn(question: str) -> TurnRecord:
    return {"id": 1, "asked_at": "", "question": question, "answer": "a"}


def test_fallback_title_trims_a_long_question() -> None:
    assert _fallback_title([_turn(" ".join(f"w{i}" for i in range(20)))]) == (
        "w0 w1 w2 w3 w4 w5 w6 w7..."
    )


def test_fallback_title_keeps_a_short_question_whole() -> None:
    assert _fallback_title([_turn("why this major")]) == "why this major"


async def test_cancelled_titling_still_leaves_a_title(store: HistoryStore) -> None:
    """The bug: aclose() cancels background title tasks, which used to leave title NULL."""
    s = Session(EventBus(), _SlowLLM(), history=store, history_enabled=True)
    s._persist_turn("why did you choose that particular major", "because ...")
    conv_id = s._conversation_id
    assert conv_id is not None

    task = asyncio.create_task(s._finalize_conversation(conv_id))
    await asyncio.sleep(0.05)  # let it write the fallback and block on the LLM
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    detail = store.get_conversation(conv_id)
    assert detail is not None
    assert detail["title"] == "why did you choose that particular major"
    store.close()


async def test_generated_title_upgrades_the_fallback(store: HistoryStore) -> None:
    s = Session(EventBus(), _TitlingLLM(), history=store, history_enabled=True)
    s._persist_turn("why did you choose that particular major", "because ...")
    conv_id = s._conversation_id
    assert conv_id is not None
    await s._finalize_conversation(conv_id)

    detail = store.get_conversation(conv_id)
    assert detail is not None
    assert detail["title"] == "A Proper Generated Title"
    store.close()
