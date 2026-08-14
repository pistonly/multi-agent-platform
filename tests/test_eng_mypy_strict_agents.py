"""Tests for eng experiment (55634575) PR4 — promote agents.py to mypy
strict alongside topics/experiments/projects.

PR4 expands the strict surface from 3 directories to 4. ``agents.py``
already passes ``mypy --strict`` cleanly (no pre-existing gaps —
the authz PR2 capability refactor was already strict-friendly), so
this PR is mechanical: pyproject override + CI step + tests.

Pins:
1. ``server/api/agents.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists topics + experiments + projects + agents in
   one override block under ``strict = true``.
3. Baseline mypy (project config) is clean for agents.py.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
PYPROJECT = f"{PROJECT_ROOT}/pyproject.toml"


def _run_mypy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def _read_pyproject() -> str:
    with open(PYPROJECT, encoding="utf-8") as fh:
        return fh.read()


def test_agents_py_passes_mypy_strict():
    """``server/api/agents.py`` passes ``mypy --strict``.

    No pre-existing type gaps. The authz PR2 capability refactor
    (``Agent.has_capability``, ``persona`` property) preserved strict
    compatibility.
    """
    result = _run_mypy("--strict", "server/api/agents.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "agents.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"found {len(file_errors)} error(s) in server-side mypy output:\n" + "\n".join(file_errors) +
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_four_to_strict():
    """pyproject must list topics + experiments + projects + agents in
    one override block under ``strict = true``. Catches accidental
    removal in future pyproject edits.
    """
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[.*?"
        r"\"server\.api\.topics\".*?"
        r"\"server\.api\.experiments\".*?"
        r"\"server\.api\.projects\".*?"
        r"\"server\.api\.agents\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "topics + experiments + projects + agents to strict = true in a "
        "single module list"
    )


def test_baseline_mypy_clean_for_agents_py():
    """Sanity: with the pyproject config (which now promotes agents.py to
    strict), running mypy on agents.py alone is still clean. Protects
    against accidental demotion in a future pyproject edit.
    """
    result = _run_mypy("server/api/agents.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "agents.py:" in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
