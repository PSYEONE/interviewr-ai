"""GET /system/info: the two model lists are sized against each other, so a caller mid-choice
can say what it is choosing rather than being told about the configuration it is replacing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import app
import app.system as system_pkg
from app.system.disk import DiskInfo
from app.system.hardware import GpuInfo, SystemInfo

_GB = 1024


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    # An 8 GB discrete card with a 3B chat model and small.en saved: the maintainer's setup, and
    # the one where the labels disagreed between the wizard and the model manager.
    app.state.settings = Settings(  # type: ignore[call-arg]
        llm_model="llama3.2:3b", whisper_model="small.en", _env_file=None
    )
    gpu = GpuInfo(index=1, name="RTX 3070", vendor="nvidia", kind="discrete", vram_mb=8 * _GB)
    detected = SystemInfo(gpus=[gpu], cpu_count=16, ram_mb=32000, gpu_error=None)
    # Patched on the package that builds the report, so no vulkaninfo or nvidia-smi runs here.
    monkeypatch.setattr(system_pkg, "detect_system", lambda: detected)
    monkeypatch.setattr(
        system_pkg,
        "probe_disk",
        lambda _override: DiskInfo(
            path="/models", total_mb=500 * _GB, free_mb=100 * _GB, models_mb=0, partial_mb=0
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _fit_of(models: object, name: str) -> str:
    """The fit label the API reported for one model, from the parsed JSON."""
    assert isinstance(models, list)
    return str(next(m["fit"] for m in models if m["name"] == name))


def _fit(body: dict[str, Any], name: str) -> str:
    return _fit_of(body["recommendation"]["catalog"], name)


def _speech_fit(body: dict[str, Any], name: str) -> str:
    return _fit_of(body["whisper"]["models"], name)


async def test_chat_models_are_sized_against_the_speech_model_being_chosen(
    client: httpx.AsyncClient,
) -> None:
    saved = (await client.get("/system/info")).json()
    choosing = (await client.get("/system/info?whisper_model=medium.en")).json()
    # The reported fault: tight while the old small.en was still assumed, too_big once applied.
    assert _fit(saved, "qwen3:8b") == "tight"
    assert _fit(choosing, "qwen3:8b") == "too_big"
    assert choosing["recommendation"]["usable_vram_mb"] < saved["recommendation"]["usable_vram_mb"]


async def test_speech_models_can_be_sized_against_the_recommended_chat_model(
    client: httpx.AsyncClient,
) -> None:
    """On a first run nothing is configured, so without the flag speech is sized against a card
    with no chat model on it and the biggest model looks free."""
    app.state.settings = Settings(  # type: ignore[call-arg]
        llm_model="", whisper_model="small.en", _env_file=None
    )
    fresh = (await client.get("/system/info")).json()
    wizard = (await client.get("/system/info?assume_recommended_llm=true")).json()
    assert fresh["whisper"]["suggested"] == "medium.en"  # judged against an empty card
    assert wizard["whisper"]["suggested"] == "small.en"  # leaves room for the recommended model
    assert _speech_fit(fresh, "medium.en") == "fits"


async def test_an_unknown_query_parameter_is_ignored(client: httpx.AsyncClient) -> None:
    # ?token= rides along on every authed request, so undeclared params must not 422.
    assert (await client.get("/system/info?token=&nonsense=1")).status_code == 200
