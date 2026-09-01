"""POST /ollama/pull's disk pre-check: refuse what won't fit, fall open on every unknown.

ASGI transport with app.state injected, so the lifespan never runs (no LLM, producer or audio).
The disk probe and the registry lookup are monkeypatched on `main`, since the route calls them
directly rather than taking them as dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from app import main
from app.config import Settings
from app.main import app
from app.system.disk import DiskInfo

_GB = 1024  # in MB, the unit DiskInfo uses

# A 6 GB model: one big weights layer plus two trivial ones.
_LAYERS = [("sha256:aaa", 6 * 1024**3), ("sha256:bbb", 2_000), ("sha256:cfg", 500)]


class FakeAdmin:
    """Stands in for OllamaAdmin so a pull that passes the guard streams without a daemon."""

    pulled: list[str] = []

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def pull_model(self, model: str) -> AsyncIterator[object]:
        FakeAdmin.pulled.append(model)
        return
        yield  # pragma: no cover - makes this an async generator

    async def close(self) -> None:
        return None


def _disk(free_mb: int) -> DiskInfo:
    return DiskInfo(
        path="/models", total_mb=500 * _GB, free_mb=free_mb, models_mb=9 * _GB, partial_mb=0
    )


@pytest.fixture(autouse=True)
def wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAdmin.pulled = []
    monkeypatch.setattr(main, "OllamaAdmin", FakeAdmin)
    monkeypatch.setattr(main, "present_blobs", lambda _dir: set())


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app.state.settings = Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _probes(
    monkeypatch: pytest.MonkeyPatch,
    disk: DiskInfo | None,
    layers: object,
    resolution: str = "found",
) -> None:
    """`layers=None` means the registry couldn't answer, which must fall open, not 404."""

    async def fake_resolve(model: str) -> tuple[str, object]:
        return ("unknown" if layers is None and resolution == "found" else resolution), layers

    monkeypatch.setattr(main, "probe_disk", lambda _override: disk)
    monkeypatch.setattr(main, "resolve_model", fake_resolve)


async def test_refuses_a_model_that_will_not_fit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probes(monkeypatch, _disk(free_mb=4 * _GB), _LAYERS)
    resp = await client.post("/ollama/pull", json={"model": "phi4"})
    assert resp.status_code == 409
    assert "6.0 GB" in resp.json()["detail"] and "4.0 GB" in resp.json()["detail"]
    assert FakeAdmin.pulled == []  # refused before the download started


async def test_allows_a_model_that_fits(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probes(monkeypatch, _disk(free_mb=40 * _GB), _LAYERS)
    resp = await client.post("/ollama/pull", json={"model": "phi4"})
    assert resp.status_code == 200
    assert FakeAdmin.pulled == ["phi4"]


async def test_headroom_blocks_an_exact_fit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filling the disk to the last byte would break the daemon's temp files."""
    _probes(monkeypatch, _disk(free_mb=6 * _GB), _LAYERS)
    assert (await client.post("/ollama/pull", json={"model": "phi4"})).status_code == 409


async def test_blobs_already_present_cost_nothing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-pulling a tag that shares every blob must not be charged the manifest size."""
    _probes(monkeypatch, _disk(free_mb=2 * _GB), _LAYERS)
    monkeypatch.setattr(main, "present_blobs", lambda _dir: {"sha256-aaa", "sha256-bbb"})
    resp = await client.post("/ollama/pull", json={"model": "llama3.1:latest"})
    assert resp.status_code == 200


async def test_falls_open_when_the_registry_cannot_answer(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probes(monkeypatch, _disk(free_mb=1), None)
    assert (await client.post("/ollama/pull", json={"model": "hf.co/x/y"})).status_code == 200


async def test_falls_open_when_the_model_store_is_unlocatable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probes(monkeypatch, None, _LAYERS)
    assert (await client.post("/ollama/pull", json={"model": "phi4"})).status_code == 200


async def test_skips_the_check_for_a_remote_daemon(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote Ollama fills its own disk, not ours, so our free space is irrelevant."""
    app.state.settings = Settings(  # type: ignore[call-arg]
        llm_model="m", ollama_base_url="http://192.168.1.50:11434", _env_file=None
    )

    def boom(_override: object) -> DiskInfo | None:
        raise AssertionError("the disk must not be probed for a remote daemon")

    monkeypatch.setattr(main, "probe_disk", boom)
    assert (await client.post("/ollama/pull", json={"model": "phi4"})).status_code == 200


async def test_refuses_a_model_the_registry_does_not_have(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo would otherwise start a download that fails after the UI showed a progress bar."""
    _probes(monkeypatch, _disk(free_mb=40 * _GB), None, resolution="missing")
    resp = await client.post("/ollama/pull", json={"model": "gemma3:4bb"})
    assert resp.status_code == 404
    assert "gemma3:4bb" in resp.json()["detail"]
    assert FakeAdmin.pulled == []  # never reached the daemon


async def test_a_registry_that_cannot_answer_is_not_a_missing_model(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offline, or a private HF repo answering 401: fall open rather than claim it doesn't exist."""
    _probes(monkeypatch, _disk(free_mb=40 * _GB), None, resolution="unknown")
    resp = await client.post("/ollama/pull", json={"model": "hf.co/someone/private"})
    assert resp.status_code == 200
    assert FakeAdmin.pulled == ["hf.co/someone/private"]


async def test_missing_is_refused_even_for_a_remote_daemon(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tag not existing has nothing to do with whose disk it would land on."""
    app.state.settings = Settings(  # type: ignore[call-arg]
        llm_model="m", ollama_base_url="http://192.168.1.50:11434", _env_file=None
    )
    _probes(monkeypatch, None, None, resolution="missing")
    assert (await client.post("/ollama/pull", json={"model": "nope"})).status_code == 404
