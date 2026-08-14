"""Tests for eng experiment (55634575) PR5 — promote
server/services/project_service.py to mypy strict.

This is the FIRST services-layer module to join the strict surface
(after the 4 server/api modules in PR2-PR4). It opens the
"services (subdir-by-subdir)" rollout promised in the PR4 log.

Pre-existing gap fixed in PR5:
* ``server/services/project_service.py:204`` — ``open_topics_map``
  had a bare ``list`` annotation, which mypy --strict rejects. Fixed
  to ``dict[uuid.UUID, list[TopicSummaryRead]]`` with the import
  added at the top of the file.

Pins:
1. ``server/services/project_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 4 server/api modules + project_service
   in one override block under ``strict = true``.
3. Baseline mypy (project config) clean for project_service.py.
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


def test_project_service_py_passes_mypy_strict():
    """``server/services/project_service.py`` passes ``mypy --strict``.

    One pre-existing type-arg gap fixed: ``open_topics_map`` was typed
    as ``dict[uuid.UUID, list]`` — now ``dict[uuid.UUID, list[TopicSummaryRead]]``
    with the ``TopicSummaryRead`` import added.
    """
    result = _run_mypy("--strict", "server/services/project_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "project_service.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"found {len(file_errors)} error(s) in server-side mypy output:\n" + "\n".join(file_errors) +
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_five_to_strict():
    """pyproject must list topics + experiments + projects + agents +
    project_service in one override block under ``strict = true``.
    """
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[.*?"
        r"\"server\.api\.topics\".*?"
        r"\"server\.api\.experiments\".*?"
        r"\"server\.api\.projects\".*?"
        r"\"server\.api\.agents\".*?"
        r"\"server\.services\.project_service\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "topics + experiments + projects + agents + project_service "
        "to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_project_service_py():
    """Sanity: with the pyproject config (which promotes project_service
    to strict), running mypy on it alone is still clean.
    """
    result = _run_mypy("server/services/project_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "project_service.py:" in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_open_topics_map_uses_typed_list_annotation():
    """Regression guard: ``open_topics_map`` must use ``list[TopicSummaryRead]``
    (not bare ``list``) so the strict surface survives future edits.
    """
    path = f"{PROJECT_ROOT}/server/services/project_service.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    no_comments = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    # The annotation that was broken in PR5's pre-existing gap was
    # ``dict[uuid.UUID, list]``. Search for the exact substring that
    # would indicate a regression.
    assert "dict[uuid.UUID, list]" not in no_comments, (
        "server/services/project_service.py open_topics_map must use "
        "``list[TopicSummaryRead]`` (or another typed list), not bare "
        "``list``. Found bare ``list`` annotation."
    )
