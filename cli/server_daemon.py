"""Backend process body for ``map server start`` / ``map server run``.

``map server`` spawns this module (``python -m cli.server_daemon``) so the
CLI stays importable even when the optional ``server`` package is absent. It owns no
CLI surface of its own: it reads ``MAP_PORT`` / ``MAP_DATABASE_URL`` from the
environment set by the parent and hands off to ``server.main.run`` — the same entry
point as the ``map-server`` console script, so foreground and background behave
identically (including uvicorn's signal handling and the startup self-check).
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger("map-server-daemon")


def main() -> None:
    # The real server lives in the ``multi-agent-platform-server`` extra's site-packages.
    # Fail loudly (non-zero, captured by the parent into the log) instead of letting a
    # bare ImportError look like a server crash.
    try:
        from server.main import run
    except ImportError as exc:
        _LOG.critical(
            "Can't start the server: the `server` package is not installed. "
            "Install it with: pip install multi-agent-platform-server"
        )
        raise SystemExit(1) from exc

    # ``run()`` resolves MAP_PORT / MAP_DATABASE_URL etc. from the env we inherited.
    run()


if __name__ == "__main__":
    main()
