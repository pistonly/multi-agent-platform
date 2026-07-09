"""Tests for eng experiment (55634575) PR5h — promote
server/services/topic_work_item_service.py to mypy strict.

Twelfth strict module (4 API + 8 services).

Pre-existing gaps fixed (4 total):

* Lines 59, 60, 68 — three ``# type: ignore[arg-type]`` comments on
  the ``to_read`` model ctor of ``TopicWorkItemRead`` that strict
  mypy no longer requires (the model accepts ``str`` for ``kind`` /
  ``priority`` / ``clear_action`` without complaint). Removed.
* Line 458 — ``advance_round_pending_since=topic.advance_round_pending_since``
  passed to ``PendingRoundAckTodoRead`` whose schema renamed the
  field to ``stale_since`` (advance_round_pending_since is now a
  DEPRECATED computed alias). Renamed the kwarg to ``stale_since``
  — same pattern as the fix in ``todo_service.py`` (PR5e line 102).

Pins:
1. ``server/services/topic_work_item_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 12 modules in one override block.
3. Baseline mypy (project config) clean.
4. No ``# type: ignore[arg-type]`` comments in the ``to_read`` body.
5. ``PendingRoundAckTodoRead(...)`` uses ``stale_since=`` kwarg, not
   the deprecated ``advance_round_pending_since=``.
"""

from __future__ import annotations

import re
import subprocess
import sys

PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"
PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"
TARGET = f"{PROJECT_ROOT}/server/services/topic_work_item_service.py"
TARGET_BASENAME = "topic_work_item_service.py"


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


def test_topic_work_item_service_passes_mypy_strict():
    """``server/services/topic_work_item_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/topic_work_item_service.py")
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


def test_pyproject_promotes_all_twelve_to_strict():
    """pyproject must list all 12 modules in one override block."""
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
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 12 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_topic_work_item_service():
    """Sanity: with the pyproject config (which promotes
    topic_work_item_service to strict), running mypy on it alone is
    still clean for the target file.
    """
    result = _run_mypy("server/services/topic_work_item_service.py")
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


def test_to_read_has_no_unused_type_ignores():
    """Regression guard: the three ``# type: ignore[arg-type]``
    comments on lines 59 / 60 / 68 of the ``to_read`` ctor were
    pre-existing strict gaps. After PR5h they must be removed.
    """
    text = _read(TARGET)
    assert "# type: ignore[arg-type]" not in text, (
        "to_read() body must not carry unused type: ignore comments — "
        "PR5h removed them. TopicWorkItemRead accepts str for kind / "
        "priority / clear_action without complaint."
    )


def test_pending_round_ack_uses_stale_since_kwarg():
    """Regression guard: ``PendingRoundAckTodoRead(...)`` must use the
    new ``stale_since=`` kwarg, not the deprecated
    ``advance_round_pending_since=``. The schema renamed the field
    (the old name is now a computed DEPRECATED alias property).
    """
    text = _read(TARGET)
    assert "stale_since=topic.advance_round_pending_since" in text, (
        "PR5h renamed the kwarg from advance_round_pending_since to "
        "stale_since — same pattern as the PR5e todo_service fix."
    )
    assert (
        "PendingRoundAckTodoRead(\n"
        "                topic_id=item.topic_id,"
    ) in text or (
        "PendingRoundAckTodoRead(\n"
        "                topic_id=item.topic_id,\n"
        "                topic_title=item.topic_title,\n"
        "                discussion_round=topic.discussion_round,\n"
        "                round_summary_count=int(topic.round_summary_count or 0),\n"
        "                summary_comment_id=item.source_comment_id,\n"
        "                summary_excerpt=item.excerpt or None,\n"
        "                stale_since=topic.advance_round_pending_since,\n"
        "                updated_at=topic.updated_at,"
    ) in text or "stale_since=topic.advance_round_pending_since" in text, (
        "PR5h should pass stale_since=... to PendingRoundAckTodoRead"
    )
