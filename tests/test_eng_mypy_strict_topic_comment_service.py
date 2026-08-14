"""Tests for eng experiment (55634575) PR5f — promote
server/services/topic_comment_service.py to mypy strict.

Sixth services-layer module on the strict surface.

Pre-existing gaps fixed (4 total):

* Lines 75 / 188 / 220 — three sites that passed
  ``kind=_comment_kind(comment).value`` (typed ``str``) where the
  schema field expects ``TopicCommentKind`` enum. Fixed by dropping
  the ``.value`` call — Pydantic v2 serializes enums to their value
  by default on JSON dump, so the JSON wire format is identical.
* Line 205 — call to untyped ``topic_comment_order_clauses()``
  helper. Fixed by annotating the helper (and its ``_desc``
  sibling) in ``server/services/thread_activity.py`` with the
  explicit ``tuple[ColumnElement[bool], ...]`` return type.

Pins:
1. ``server/services/topic_comment_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 10 modules in one override block.
3. Baseline mypy (project config) clean.
4. ``_comment_kind(...)`` is now passed directly to the schema
   (no ``.value``) in all three call sites.
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


def test_topic_comment_service_passes_mypy_strict():
    """``server/services/topic_comment_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/topic_comment_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "topic_comment_service.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"found {len(file_errors)} error(s) in server-side mypy output:\n" + "\n".join(file_errors) +
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_ten_to_strict():
    """pyproject must list all 10 modules in one override block."""
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[.*?"
        r"\"server\.api\.topics\".*?"
        r"\"server\.api\.experiments\".*?"
        r"\"server\.api\.projects\".*?"
        r"\"server\.api\.agents\".*?"
        r"\"server\.services\.project_service\".*?"
        r"\"server\.services\.phase_service\".*?"
        r"\"server\.services\.experiment_capabilities_service\".*?"
        r"\"server\.services\.mention_service\".*?"
        r"\"server\.services\.todo_service\".*?"
        r"\"server\.services\.topic_comment_service\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 10 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_topic_comment_service():
    """Sanity: with the pyproject config (which promotes topic_comment_service
    to strict), running mypy on it alone is still clean.
    """
    result = _run_mypy("server/services/topic_comment_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "topic_comment_service.py:" in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_topic_comment_service_passes_enum_not_value():
    """Regression guard for the PR5f ``.value`` removal: the three
    schema construction sites must pass ``_comment_kind(comment)``
    (the enum) — never ``_comment_kind(comment).value`` (the str).

    Pydantic v2 serializes enums to their value on JSON dump, so the
    wire format is identical, but the static type improves.
    """
    path = f"{PROJECT_ROOT}/server/services/topic_comment_service.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "_comment_kind(comment).value" not in text, (
        "server/services/topic_comment_service.py must not call "
        "``.value`` on ``_comment_kind(...)`` — pass the enum directly. "
        "Pydantic v2 serializes enum to value on JSON dump."
    )


def test_thread_activity_order_clauses_typed():
    """Regression guard for the helper typing: ``topic_comment_order_clauses``
    and its ``_desc`` sibling must carry explicit return annotations so
    strict callers don't get ``[no-untyped-call]`` errors.
    """
    path = f"{PROJECT_ROOT}/server/services/thread_activity.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # Both helpers should have a return annotation referencing ColumnElement.
    assert "def topic_comment_order_clauses() ->" in text, (
        "topic_comment_order_clauses must carry a return type annotation"
    )
    assert "def topic_comment_order_clauses_desc() ->" in text, (
        "topic_comment_order_clauses_desc must carry a return type annotation"
    )
