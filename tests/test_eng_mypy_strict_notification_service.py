"""Tests for eng experiment (55634575) PR5i — promote
server/services/notification_service.py to mypy strict.

Thirteenth strict module (4 API + 9 services).

Pre-existing gaps fixed (10 total):

* Lines 224 / 247 / 268 / 359 / 419 / 486 / 527 — seven sites that
  typed ``payload: dict | None`` (bare ``dict``). Strict mypy requires
  ``dict[str, Any] | None`` so the untyped generic doesn't leak through.
* Line 330 — ``dialect_insert = pg_insert`` / ``sqlite_insert``
  branched assignment widens the type to the intersection of the two
  SQLAlchemy ``Insert`` classes (postgres-specific vs sqlite-specific).
  Typed as ``Any`` since the return-value flow is downstream-cast.
* Line 634 — ``mentions: list`` (bare ``list``) -> ``list[Any]`` for
  ``mark_agent_mentioned_notifications_read_no_commit``.
* Line 838 — ``Agent.id.notin_(exclude) if exclude else True`` mixed
  ``BinaryExpression[bool] | bool`` in a single ``where()`` clause
  (mypy can't narrow the conditional to a ``ColumnElement[bool]``).
  Refactored to a two-step ``if exclude: stmt = stmt.where(...)``
  chain so each ``where`` receives a homogeneous boolean expression.
* Line 348 — ``result.scalar_one()`` returned ``Any`` because the
  upstream ``stmt`` was typed ``Any`` (dialect widening). Added
  ``cast(Notification, row)`` at the return to land on the function's
  declared return type.

Pins:
1. ``server/services/notification_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 13 modules in one override block.
3. Baseline mypy (project config) clean for the target file.
4. No bare ``dict`` or ``list`` annotations in ``notification_service``.
5. ``_project_agent_ids`` uses the two-step ``if exclude`` pattern.
"""

from __future__ import annotations

import re
import subprocess
import sys

PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"
PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"
TARGET = f"{PROJECT_ROOT}/server/services/notification_service.py"
TARGET_BASENAME = "notification_service.py"


def _run_mypy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_notification_service_passes_mypy_strict():
    """``server/services/notification_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/notification_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if TARGET_BASENAME in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_thirteen_to_strict():
    """pyproject must list all 13 modules in one override block."""
    text = _read(PYPROJECT)
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
        r"\"server\.services\.agent_work_service\".*?"
        r"\"server\.services\.topic_work_item_service\".*?"
        r"\"server\.services\.notification_service\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 13 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_notification_service():
    """Sanity: with the pyproject config (which promotes
    notification_service to strict), running mypy on it alone is still
    clean for the target file.
    """
    result = _run_mypy("server/services/notification_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if TARGET_BASENAME in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s) in target file:\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_no_bare_dict_or_list_annotations():
    """Regression guard: no bare ``dict`` or ``list`` parameter
    annotations remain in notification_service — every one is now
    parameterized (mostly ``dict[str, Any]``, ``list[Any]``).
    """
    text = _read(TARGET)
    # Negative lookahead excludes parameterized `dict[...]` / `dict_foo`.
    # Bare dict/list annotation: `: dict` followed by whitespace,
    # `|`, `,`, `)`, `=`, or end-of-line — never by `[` or word char.
    bare_dict_anno = re.findall(r":\s*dict(?![\w\[])", text)
    bare_list_anno = re.findall(r":\s*list(?![\w\[])", text)
    assert not bare_dict_anno, (
        f"notification_service.py has {len(bare_dict_anno)} bare `dict` "
        f"annotation(s) — PR5i parameterized all 7 sites."
    )
    assert not bare_list_anno, (
        f"notification_service.py has {len(bare_list_anno)} bare `list` "
        f"annotation(s) — PR5i parameterized the lone `mentions: list`."
    )


def test_project_agent_ids_uses_two_step_where():
    """Regression guard: ``_project_agent_ids`` must use the
    two-step ``if exclude: stmt = stmt.where(...)`` pattern instead
    of the previous ``where(... if exclude else True)`` ternary that
    mixed ``BinaryExpression[bool] | bool``.
    """
    text = _read(TARGET)
    # Old pattern: '.where(\n            Agent.project_id == project_id,\n            Agent.id.notin_(exclude) if exclude else True,\n        )'
    assert "Agent.id.notin_(exclude) if exclude else True" not in text, (
        "_project_agent_ids must not mix BinaryExpression[bool] | bool "
        "in a single where() — PR5i split into a two-step if-exclude pattern."
    )
    assert "if exclude:" in text and "stmt.where(Agent.id.notin_(exclude))" in text, (
        "_project_agent_ids must use the two-step if-exclude: stmt = stmt.where(...) pattern."
    )
