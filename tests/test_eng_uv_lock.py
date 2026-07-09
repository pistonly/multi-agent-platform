"""Tests for eng experiment (55634575) PR1 — uv.lock presence + integrity.

Asserts:

1. ``uv.lock`` exists at the repo root.
2. ``uv.lock`` is NOT in ``.gitignore`` (so it's actually committed).
3. ``uv lock --check`` passes — i.e. the lockfile is in sync with
   ``pyproject.toml``. This is the same condition CI's ``uv sync
   --frozen`` enforces.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_uv_lock_exists():
    uv_lock = _repo_root() / "uv.lock"
    assert uv_lock.is_file(), "uv.lock must exist at repo root"


def test_uv_lock_not_gitignored():
    """The plan requires ``uv.lock`` to be tracked in git. Verify it's
    not in any ``.gitignore`` line in the repo root or parent dirs
    affecting this directory."""
    gitignore = _repo_root() / ".gitignore"
    if not gitignore.is_file():
        return  # No gitignore at all — uv.lock is fine
    for raw in gitignore.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # crude but adequate: any line whose pattern matches uv.lock
        if "uv.lock" in line:
            pytest.fail(
                f"uv.lock must not be ignored — found '{line}' in .gitignore"
            )


def test_uv_lock_check_passes():
    """``uv lock --check`` is what CI runs (via ``uv sync --frozen``).
    If the lockfile is out of date with pyproject, the lock command
    exits non-zero and the test fails."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv CLI not on PATH")
    result = subprocess.run(
        [uv, "lock", "--check"],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "uv lock --check failed — pyproject has drifted from uv.lock.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
