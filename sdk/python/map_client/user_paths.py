"""User-level ``~/.map/`` paths that must not follow a remapped ``$HOME``.

Claude Code (and some sandboxes) set ``HOME`` to a project-local directory
so ``~/.claude`` stays isolated. ``Path.home()`` / ``expanduser('~')`` honor
that remap, which previously dropped the MAP server SQLite file into
``$HOME/.map/data/map.db`` instead of the login user's real home.

Machine-level MAP state (daemon pid/log, default DB, ``admin.yaml``) uses
the passwd home. Project-local ``.map/`` is unaffected (found by walking
from cwd). Override with ``MAP_HOME`` when you truly want a custom parent
of ``.map/``.
"""
from __future__ import annotations

import os
from pathlib import Path

MAP_HOME_ENV = "MAP_HOME"


def login_home() -> Path:
    """Login user's home from the passwd database, ignoring ``$HOME``."""
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return Path.home()


def expand_login_user(path: str | Path) -> Path:
    """``Path.expanduser`` analogue whose ``~`` is ``login_home()``, not ``$HOME``."""
    raw = os.fspath(path)
    if raw == "~":
        return login_home()
    if raw.startswith("~/") or raw.startswith("~\\"):
        return login_home() / raw[2:]
    return Path(raw)


def map_user_home() -> Path:
    """Parent directory of user-level ``.map/`` (``MAP_HOME`` or login home)."""
    override = os.environ.get(MAP_HOME_ENV, "").strip()
    if override:
        return expand_login_user(override)
    return login_home()


def map_state_dir() -> Path:
    """User-level ``.map/`` (DB, daemon pid/log, ``admin.yaml``)."""
    return map_user_home() / ".map"


def default_sqlite_url() -> str:
    return f"sqlite:///{map_state_dir() / 'data' / 'map.db'}"
