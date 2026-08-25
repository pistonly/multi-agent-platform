"""User-level ~/.map paths ignore a remapped $HOME (Claude Code sandbox)."""
from __future__ import annotations

from pathlib import Path

from map_client.user_paths import (
    default_sqlite_url,
    expand_login_user,
    login_home,
    map_state_dir,
    map_user_home,
)


def test_login_home_ignores_remapped_home(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "claude_home_zai"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.delenv("MAP_HOME", raising=False)
    assert Path.home() == fake
    assert login_home() != fake
    assert map_user_home() == login_home()
    assert map_state_dir() == login_home() / ".map"
    assert default_sqlite_url().endswith("/.map/data/map.db")
    assert str(fake) not in default_sqlite_url()


def test_map_home_override(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "explicit-home"
    monkeypatch.setenv("MAP_HOME", str(override))
    monkeypatch.setenv("HOME", str(tmp_path / "ignored-home"))
    assert map_user_home() == override
    assert map_state_dir() == override / ".map"


def test_tilde_expands_to_login_home_not_env_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "claude_home_zai"))
    monkeypatch.delenv("MAP_HOME", raising=False)
    assert expand_login_user("~") == login_home()
    assert expand_login_user("~/.map") == login_home() / ".map"


def test_settings_default_db_ignores_home_remap(monkeypatch, tmp_path: Path) -> None:
    from server.config import Settings, get_settings

    monkeypatch.setenv("HOME", str(tmp_path / "claude_home_zai"))
    monkeypatch.delenv("MAP_HOME", raising=False)
    monkeypatch.delenv("MAP_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        url = Settings().database_url
    finally:
        get_settings.cache_clear()
    assert str(tmp_path / "claude_home_zai") not in url
    assert url == default_sqlite_url()
