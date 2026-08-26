"""Locate the alembic.ini shipped with MAP and run the Alembic CLI.

Checkout / Docker image: ``alembic.ini`` lives at the working directory
(repo root or ``/app``). Wheel install: the same file is copied to
``server/_migrate/alembic.ini`` at pack time (T29).

Usage::

    alembic upgrade head                 # from a checkout
    python -m server.migrate upgrade head  # from a wheel
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BUNDLED_INI = Path(__file__).resolve().parent / "_migrate" / "alembic.ini"


def resolve_alembic_ini() -> Path:
    """Return alembic.ini from cwd (dev/Docker) or the wheel bundle."""
    cwd_ini = Path.cwd() / "alembic.ini"
    if cwd_ini.is_file():
        return cwd_ini.resolve()
    if _BUNDLED_INI.is_file():
        return _BUNDLED_INI.resolve()
    raise FileNotFoundError(
        "alembic.ini not found in the current directory or in the wheel bundle "
        "(server/_migrate). Run from the repository root, or install a wheel "
        "built with scripts/check-packaging.sh."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ini = resolve_alembic_ini()
    return subprocess.call(["alembic", "-c", str(ini), *args])


if __name__ == "__main__":
    raise SystemExit(main())
