"""POST /settings/reload: cheap settings reach the running engine with no restart."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest

from app.config import Settings, get_settings
from app.config_store import apply_config
from app.events import EventBus
from app.llm.base import STYLE_DIRECTIVES, Message
from app.main import app
from app.session import Session


class _FakeLLM:
    async def check_health(self) -> bool:
        return True

    async def warmup(self) -> None:
        return None

    async def generate(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        yield "ok"

    async def close(self) -> None:
        return None


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app.state.settings = Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]
    app.state.retriever = None
    app.state.llm = None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_reload_swaps_style_directives_without_a_restart(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing a style directive must reach a live session: tuning prompt wording is iterative,
    and a restart per edit is what makes it unusable."""
    env = tmp_path / ".env"
    env.write_text("INTERVIEWR_LLM_MODEL=m\n")
    apply_config(
        Settings(llm_model="m", _env_file=None),  # type: ignore[call-arg]
        {"answer_style_bullets": "exactly two bullets"},
        env_path=env,
    )
    monkeypatch.setitem(Settings.model_config, "env_file", env)
    get_settings.cache_clear()

    session = Session(EventBus(), _FakeLLM())
    app.state.session = session
    session.set_answer_style("bullets")
    assert session._style_directive() == STYLE_DIRECTIVES["bullets"]

    resp = await client.post("/settings/reload")
    assert resp.status_code == 200
    assert session._style_directive() == "exactly two bullets"

    get_settings.cache_clear()  # the cache outlives this test's monkeypatched env file
