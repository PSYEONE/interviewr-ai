"""Where config and state live, which differs between a checkout and an installed copy.

Packaging moved these off the source tree. The dev layout has to stay exactly where it was, because
the maintainer's .env and databases are already there, and an installed copy under /usr/lib has
nowhere writable so it has to land in the XDG directories instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty HOME with no XDG variables set, so the fallbacks are what gets exercised."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "INTERVIEWR_CONFIG_DIR", "INTERVIEWR_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def as_checkout(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(config, "_is_checkout", lambda: value)


class TestCheckout:
    def test_config_stays_at_the_repo_root(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        as_checkout(monkeypatch, True)
        assert config.config_dir() == config._REPO_ROOT

    def test_state_stays_under_backend_data(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_checkout(monkeypatch, True)
        assert config.state_dir() == config._BACKEND_DIR / "data"


class TestInstalled:
    def test_config_falls_back_to_dot_config(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_checkout(monkeypatch, False)
        assert config.config_dir() == home / ".config" / "interviewr"

    def test_state_falls_back_to_local_share(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_checkout(monkeypatch, False)
        assert config.state_dir() == home / ".local" / "share" / "interviewr"

    def test_xdg_variables_are_honoured(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_checkout(monkeypatch, False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/etc/xdg-test")
        monkeypatch.setenv("XDG_DATA_HOME", "/srv/data-test")
        assert config.config_dir() == Path("/etc/xdg-test/interviewr")
        assert config.state_dir() == Path("/srv/data-test/interviewr")

    def test_a_relative_xdg_value_is_ignored(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The spec says a relative XDG path is invalid and must be ignored. Resolving it against
        # the cwd would scatter config into whatever directory the engine happened to start in.
        as_checkout(monkeypatch, False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
        assert config.config_dir() == home / ".config" / "interviewr"


class TestOverrides:
    def test_explicit_overrides_win_over_both_layouts(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INTERVIEWR_CONFIG_DIR", "/opt/cfg")
        monkeypatch.setenv("INTERVIEWR_STATE_DIR", "/opt/state")
        for checkout in (True, False):
            as_checkout(monkeypatch, checkout)
            assert config.config_dir() == Path("/opt/cfg")
            assert config.state_dir() == Path("/opt/state")


class TestDbPaths:
    def test_databases_default_into_the_state_dir(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_checkout(monkeypatch, False)
        settings = config.Settings(llm_model="m", _env_file=None)  # type: ignore[call-arg]
        state = home / ".local" / "share" / "interviewr"
        assert settings.resolved_rag_db_path() == state / "rag.sqlite3"
        assert settings.resolved_history_db_path() == state / "history.sqlite3"

    def test_an_explicit_path_still_wins(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_checkout(monkeypatch, False)
        settings = config.Settings(  # type: ignore[call-arg]
            llm_model="m",
            rag_db_path="/tmp/rag.db",
            history_db_path="/tmp/hist.db",
            _env_file=None,
        )
        assert settings.resolved_rag_db_path() == Path("/tmp/rag.db")
        assert settings.resolved_history_db_path() == Path("/tmp/hist.db")


class TestIsCheckout:
    def test_a_clone_is_a_checkout(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        monkey = pytest.MonkeyPatch()
        monkey.setattr(config, "_REPO_ROOT", tmp_path)
        assert config._is_checkout() is True
        monkey.undo()

    def test_a_tree_without_git_is_not(self, tmp_path: Path) -> None:
        # What an installed copy under /usr/lib/interviewr looks like: source present, no .git.
        monkey = pytest.MonkeyPatch()
        monkey.setattr(config, "_REPO_ROOT", tmp_path)
        assert config._is_checkout() is False
        monkey.undo()
