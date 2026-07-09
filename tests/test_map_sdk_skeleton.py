"""Tests for arch experiment (0519e2a3) PR1 — map_sdk skeleton.

Verifies:

1. ``map_sdk`` is importable from the project root (pyproject include).
2. ``map_sdk`` exposes ``__version__`` and a smoke helper.
3. Hard boundary: ``map_sdk`` does not import from ``server.*``. This
   is the plan's "grep -r 'from server' map_sdk/ 零结果" acceptance
   criterion, expressed as a test so CI catches regression.
4. The CLI module imports successfully alongside ``map_sdk`` (no
   import-time circular dependency).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_map_sdk_importable():
    import map_sdk

    assert map_sdk.__version__
    assert isinstance(map_sdk.__version__, str)


def test_map_sdk_hello_returns_expected_string():
    import map_sdk

    assert map_sdk.hello() == "map_sdk ok"


def test_map_sdk_does_not_import_server():
    """Hard boundary: ``map_sdk`` MUST NOT depend on ``server.*``.

    The plan's PR1 acceptance: ``grep -r 'from server' map_sdk/`` 零
    结果. We run the grep from Python so the test fails on regression
    in any environment with grep available.
    """
    repo_root = Path(__file__).resolve().parents[1]
    map_sdk_dir = repo_root / "sdk" / "python" / "map_sdk"
    assert map_sdk_dir.is_dir(), f"map_sdk package missing at {map_sdk_dir}"

    result = subprocess.run(
        ["grep", "-r", "-l", "from server", str(map_sdk_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 or not result.stdout.strip(), (
        "map_sdk must not import from server.* — found:\n"
        + (result.stdout or "(grep exited 1 but no files listed)")
    )


def test_map_sdk_appears_in_pyproject_includes():
    """The plan requires pyproject's ``include = [...]`` to list
    ``map_sdk*`` so install/sdist pickup the new package."""
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = (repo_root / "pyproject.toml").read_text()
    assert "map_sdk*" in pyproject, (
        "pyproject.toml must include `map_sdk*` in setuptools packages.find"
    )


def test_cli_imports_with_map_sdk_present():
    """Sanity: ``cli.main`` imports successfully when ``map_sdk`` is on
    the path. Catches missing-package + circular-import regressions."""
    import cli.main  # noqa: F401  (import side effect is the test)

    # Optional: the version alias should be a string when CLI loads.
    from cli.main import _map_sdk_version

    assert isinstance(_map_sdk_version, str)
