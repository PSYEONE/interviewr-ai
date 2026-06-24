"""Answer-style directive: command parsing, the sticky setter, and reaching the LLM."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.events import AnswerStyleCommand, EventBus, parse_command
from app.llm.base import Message
from app.session import Session


class _FakeLLM:
    """Captures the messages of the last generate() call so a test can inspect the prompt."""

    def __init__(self) -> None:
        self.last: list[Message] = []

    async def check_health(self) -> bool:
        return True

    async def warmup(self) -> None:
        return None

    async def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        self.last = list(messages)
        yield "ok"

    async def close(self) -> None:
        return None


def test_parse_answer_style_command() -> None:
    cmd = parse_command('{"type":"answer_style","style":"bullets"}')
    assert isinstance(cmd, AnswerStyleCommand)
    assert cmd.style == "bullets"


def test_set_answer_style_changes_directive() -> None:
    s = Session(EventBus(), _FakeLLM())
    assert "spoken script" in s._style_directive()  # default
    s.set_answer_style("bullets")
    assert "bullet points" in s._style_directive()
    s.set_answer_style("nonsense")  # unknown style is ignored, keeps the last valid one
    assert "bullet points" in s._style_directive()


async def test_directive_reaches_the_llm() -> None:
    llm = _FakeLLM()
    s = Session(EventBus(), llm)
    s.set_answer_style("bullets")
    s.add_transcript("what is your greatest weakness")
    s.request_answer()
    assert s._gen_task is not None
    await s._gen_task
    user_msg = llm.last[-1]["content"]
    assert "what is your greatest weakness" in user_msg
    assert "bullet points" in user_msg  # the directive rides the ephemeral turn
