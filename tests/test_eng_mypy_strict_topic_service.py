"""Tests for eng experiment (55634575) PR5j — promote
server/services/topic_service.py to mypy strict.

Fourteenth strict module (4 API + 10 services). Last planned module
in the strict rollout: topic_service is the largest domain service
(1500+ lines).

Pre-existing gaps fixed (8 total):

* Line 181 — ``advance_round_pending_since=topic.advance_round_pending_since``
  passed to ``TopicSummaryRead`` whose schema renamed the field to
  ``stale_since`` (advance_round_pending_since is now a DEPRECATED
  computed alias property). Renamed kwarg.
* Line 349 — ``groups`` dict typed ``dict[tuple[UUID, UUID], ...]`` but
  the actual key includes ``owner_agent_id`` which is nullable
  (``UUID | None``) per the model. Widened the key tuple.
* Line 446 — ``category=item.category`` where ``item.category`` is
  ``str | None`` from the column, but ``TopicActionItemRead.category``
  expects ``ActionItemCategory | None``. Convert via the enum ctor.
* Line 644 — ``audit_entries: list[dict[str, object]]`` — the ``object``
  values can never satisfy ``log_no_commit(action: str, target_id:
  UUID | None, payload: dict[Any, Any] | None)``. Widened to
  ``dict[str, Any]``.
* Lines 803 / 852 — ``-> dict`` (bare ``dict``) returns on
  ``_complete_action_item_no_commit`` and ``deliver_action_item_no_commit``.
  Typed as ``-> dict[str, Any]``.

Pins:
1. ``server/services/topic_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 14 modules in one override block.
3. Baseline mypy (project config) clean for the target file.
4. ``TopicSummaryRead(...)`` uses ``stale_since=`` kwarg.
5. ``groups`` key tuple is ``tuple[uuid.UUID, uuid.UUID | None]``.
6. ``category`` converted via ``ActionItemCategory(...)`` ctor.
7. ``audit_entries`` typed ``list[dict[str, Any]]``.
8. Both action-item helpers return ``dict[str, Any]``.
"""

from __future__ import annotations

import re
import subprocess
import sys

PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"
PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"
TARGET = f"{PROJECT_ROOT}/server/services/topic_service.py"
TARGET_BASENAME = "topic_service.py"


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


def test_topic_service_passes_mypy_strict():
    """``server/services/topic_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/topic_service.py")
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


def test_pyproject_promotes_all_fourteen_to_strict():
    """pyproject must list all 14 modules in one override block."""
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
        r"\"server\.services\.topic_service\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 14 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_topic_service():
    """Sanity: with the pyproject config (which promotes topic_service
    to strict), running mypy on it alone is still clean for the target.
    """
    result = _run_mypy("server/services/topic_service.py")
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


def test_topic_summary_uses_stale_since_kwarg():
    """Regression guard: ``TopicSummaryRead(...)`` must use
    ``stale_since=``, not ``advance_round_pending_since=``.
    """
    text = _read(TARGET)
    assert "stale_since=topic.advance_round_pending_since" in text, (
        "TopicSummaryRead ctor must use stale_since= kwarg (PR5j renamed)."
    )


def test_groups_dict_key_allows_optional_owner():
    """Regression guard: ``groups`` key tuple is
    ``(uuid.UUID, uuid.UUID | None)`` because ``owner_agent_id`` is
    nullable on ``TopicActionItem``.
    """
    text = _read(TARGET)
    assert "dict[tuple[uuid.UUID, uuid.UUID | None], list[TopicActionItem]]" in text, (
        "groups dict must widen its key tuple to allow owner_agent_id "
        "= None (PR5j fixed the strict type)."
    )


def test_category_uses_enum_ctor():
    """Regression guard: ``TopicActionItemRead(...)`` category field
    must convert the ``str | None`` column value via the enum ctor.
    """
    text = _read(TARGET)
    assert (
        "category=ActionItemCategory(item.category) if item.category else None"
        in text
    ), (
        "category kwarg must wrap item.category in ActionItemCategory(...) ctor "
        "since the schema field is ActionItemCategory | None, not str | None."
    )


def test_audit_entries_uses_any():
    """Regression guard: ``audit_entries`` is typed
    ``list[dict[str, Any]]`` so the entries can satisfy
    ``log_no_commit(action: str, target_id: UUID | None, payload: dict[Any, Any] | None)``.
    """
    text = _read(TARGET)
    assert "audit_entries: list[dict[str, Any]] = []" in text, (
        "audit_entries must be list[dict[str, Any]] (PR5j widened from object)."
    )


def test_action_item_helpers_return_typed_dict():
    """Regression guard: ``_complete_action_item_no_commit`` and
    ``deliver_action_item_no_commit`` return ``dict[str, Any]`` (not
    bare ``dict``).
    """
    text = _read(TARGET)
    assert "triggered_by: str = \"manual\",\n) -> dict[str, Any]:" in text or (
        'triggered_by: str = "manual",\n) -> dict[str, Any]:' in text
    ) or "triggered_by: str = \"manual\",\n) -> dict[str, Any]" in text, (
        "_complete_action_item_no_commit must return dict[str, Any]."
    )
    assert (
        "triggered_by: str = \"deliver\",\n    agent_id: uuid.UUID | None = None,\n) -> dict[str, Any]:"
        in text
    ) or (
        "agent_id: uuid.UUID | None = None,\n) -> dict[str, Any]:"
        in text and "triggered_by: str = \"deliver\"," in text
    ), (
        "deliver_action_item_no_commit must return dict[str, Any]."
    )
