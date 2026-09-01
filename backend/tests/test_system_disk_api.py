"""GET /system/disk: the cheap probe the UI polls on focus, without /system/info's cost."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from app import main
from app.config import Settings
from app.main import app
from app.system.disk import DiskInfo

_GB = 1024


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app.state.settings = Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_returns_the_probe_result(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    disk = DiskInfo(
        path="/models", total_mb=500 * _GB, free_mb=40 * _GB, models_mb=9 * _GB, partial_mb=2 * _GB
    )
    monkeypatch.setattr(main, "probe_disk", lambda _override: disk)
    resp = await client.get("/system/disk")
    assert resp.status_code == 200
    assert resp.json()["partial_mb"] == 2 * _GB


async def test_skips_the_expensive_report(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: no vulkaninfo/nvidia-smi subprocesses and no Ollama call, which is
    ~1000x the cost of reading the disk."""

    async def boom(_settings: object) -> object:
        raise AssertionError("must not build the full system report")

    monkeypatch.setattr(main, "collect_system_report", boom)
    monkeypatch.setattr(main, "probe_disk", lambda _override: None)
    assert (await client.get("/system/disk")).status_code == 200


async def test_null_when_the_store_cannot_be_found(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "probe_disk", lambda _override: None)
    assert (await client.get("/system/disk")).json() is None
