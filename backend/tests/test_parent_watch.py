"""Exiting when the launcher is gone, so an orphaned engine cannot keep the port."""

from __future__ import annotations

import os

import pytest

from app.system.parent import ENV_VAR, is_alive, parent_pid


def test_our_own_pid_is_alive() -> None:
    assert is_alive(os.getpid())


def test_a_dead_pid_is_not_alive() -> None:
    """A pid that has never existed in this boot. 0x7FFFFFFF is above any pid_max in practice."""
    assert not is_alive(0x7FFFFFFF)


def test_no_launcher_means_no_watching(monkeypatch: pytest.MonkeyPatch) -> None:
    # A manually run engine has no launcher to outlive, and must not exit on its own.
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert parent_pid() is None


def test_a_launcher_pid_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "4321")
    assert parent_pid() == 4321


def test_init_is_not_a_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """pid 1 is what an orphan gets reparented to, so it is never a launcher worth watching;
    treating it as one would mean the engine watches a process that outlives everything."""
    monkeypatch.setenv(ENV_VAR, "1")
    assert parent_pid() is None


def test_a_malformed_value_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "not-a-pid")
    assert parent_pid() is None
