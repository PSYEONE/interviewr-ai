"""Answer-style directive: command parsing, the sticky setter, and reaching the LLM."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.config import Settings
from app.events import AnswerStyleCommand, EventBus, parse_command
from app.llm.base import STYLE_DIRECTIVES, Chunk, Message, effective_style_directives
from app.session import Session


class _FakeLLM:
    """Captures the messages of the last generate() call so a test can inspect the prompt."""

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


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"llm_model": "m", "_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_effective_directives_default_to_builtin() -> None:
    assert effective_style_directives(_settings()) == STYLE_DIRECTIVES


def test_effective_directives_take_the_override() -> None:
    resolved = effective_style_directives(_settings(answer_style_bullets="exactly two bullets"))
    assert resolved["bullets"] == "exactly two bullets"
    assert resolved["script"] == STYLE_DIRECTIVES["script"]  # untouched styles keep the default


def test_session_uses_the_injected_directives() -> None:
    s = Session(EventBus(), _FakeLLM(), style_directives={"script": "one line", "bullets": "two"})
    assert s._style_directive() == "one line"
    s.set_answer_style("bullets")
    assert s._style_directive() == "two"


def test_a_style_with_an_empty_directive_can_still_be_selected() -> None:
    # Validation keys off the fixed style names, not the directive text: blanking one must not
    # make its style unreachable.
    s = Session(EventBus(), _FakeLLM(), style_directives={"script": "say it", "bullets": ""})
    s.set_answer_style("bullets")
    assert s._answer_style == "bullets"
    assert s._style_directive() == ""


def test_directives_can_be_swapped_live() -> None:
    # What POST /settings/reload does; no restart, so a running session must pick them up.
    s = Session(EventBus(), _FakeLLM())
    s.set_style_directives({"script": "new script text", "bullets": "new bullet text"})
    assert s._style_directive() == "new script text"


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
