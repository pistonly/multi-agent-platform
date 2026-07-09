"""Tests for eng experiment (55634575) PR7 — ``server.__version__``
single source of truth.

The version string used to live in two places that could drift:

* ``pyproject.toml`` — the release-of-record (``uv build`` ships this)
* ``server/main.py`` — the FastAPI app's ``version=`` kwarg, surfaced
  via ``GET /__version__`` and ``OpenAPI`` spec

PR7 introduces ``server/__version__.py`` as a single importable
source. ``server/main.py`` now imports ``__version__`` from there.
This test pins the invariant that ``pyproject.toml`` and
``server/__version__.py`` stay in lockstep, and that no other module
in ``server/`` hardcodes the version literal.

Release workflow: bump both ``pyproject.toml`` ``[project] version``
and ``server/__version__.py`` in the same commit. The two assertions
below fail otherwise.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path("/home/AI02/Documents/quantaeye/multi_agents_platform")
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
SERVER = PROJECT_ROOT / "server"


def _read_pyproject_version() -> str:
    """Extract ``[project] version = "..."`` from pyproject.toml."""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^\[project\].*?^version\s*=\s*"([^"]+)"', text, re.MULTILINE | re.DOTALL)
    assert match is not None, (
        "pyproject.toml must declare [project] version = \"...\" "
        "before [tool.*] sections — regex did not match"
    )
    return match.group(1)


def test_server_version_module_exists():
    """``server.__version__`` must be importable."""
    mod = importlib.import_module("server.__version__")
    assert hasattr(mod, "__version__"), (
        "server/__version__.py must expose `__version__` attribute"
    )
    assert isinstance(mod.__version__, str), (
        f"server.__version__ must be str, got {type(mod.__version__).__name__}"
    )
    assert mod.__version__, "server.__version__ must be a non-empty string"


def test_server_version_matches_pyproject():
    """``server.__version__`` must equal ``pyproject [project] version``.

    Release bump workflow: edit BOTH in the same commit, otherwise
    this test fails and CI blocks the merge.
    """
    mod = importlib.import_module("server.__version__")
    pyproject_version = _read_pyproject_version()
    assert mod.__version__ == pyproject_version, (
        f"server.__version__ ({mod.__version__!r}) does not match "
        f"pyproject.toml [project] version ({pyproject_version!r}). "
        f"Bump both in the same commit."
    )


def test_server_version_follows_semver_shape():
    """Version must parse as ``MAJOR.MINOR.PATCH`` with optional pre-release.

    Loose check — covers ``0.1.0``, ``0.10.0``, ``1.0.0-rc1`` etc.
    """
    mod = importlib.import_module("server.__version__")
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?", mod.__version__), (
        f"server.__version__={mod.__version__!r} is not MAJOR.MINOR.PATCH "
        f"(with optional pre-release / build metadata)."
    )


def test_no_hardcoded_version_literal_in_server_main():
    """Regression guard: ``server/main.py`` must not hardcode the
    version string. It must import from ``server.__version__``.
    """
    text = (SERVER / "main.py").read_text(encoding="utf-8")
    # Match quoted "0.1.0" or '0.1.0' anywhere — flag any literal that
    # looks like the version. Allow the literal inside a comment if
    # prefixed with `#`; everything else is a violation.
    for match in re.finditer(r"['\"](\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)['\"]", text):
        # Skip comments — find the line this match is on
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.start())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        pytest.fail(
            f"server/main.py hardcodes version literal {match.group(0)!r} "
            f"on line: {line!r}. Import from `server.__version__` instead."
        )


def test_server_main_imports_version_from_server_package():
    """Regression guard: ``server/main.py`` must import ``__version__``
    from ``server.__version__``.
    """
    text = (SERVER / "main.py").read_text(encoding="utf-8")
    assert "from server.__version__ import" in text, (
        "server/main.py must `from server.__version__ import __version__` "
        "— PR7 wired this up; do not regress to a hardcoded literal."
    )
