"""Tests for eng experiment (55634575) PR5j — promote
server/services/topic_service.py (and its split modules) to mypy strict.

``topic_service`` is now a re-export facade. Behaviour lives in:

* ``topic_helpers``
* ``topic_lifecycle_service``
* ``topic_resolve_service``
* ``topic_action_item_ops``
* ``topic_comment_service`` (earlier split)

Pins:
1. Facade + split modules pass ``mypy --strict``.
2. ``pyproject.toml`` lists the facade and split modules in the strict override.
3. Baseline mypy clean for the facade.
4. Regression strings live in the modules that own the logic.
"""

from __future__ import annotations

import re
import subprocess
import sys

PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"
PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"
TARGET = f"{PROJECT_ROOT}/server/services/topic_service.py"
TARGET_BASENAME = "topic_service.py"
LIFECYCLE = f"{PROJECT_ROOT}/server/services/topic_lifecycle_service.py"
RESOLVE = f"{PROJECT_ROOT}/server/services/topic_resolve_service.py"
ACTION_OPS = f"{PROJECT_ROOT}/server/services/topic_action_item_ops.py"
SPLIT_MODULES = [
    "server/services/topic_service.py",
    "server/services/topic_helpers.py",
    "server/services/topic_lifecycle_service.py",
    "server/services/topic_resolve_service.py",
    "server/services/topic_action_item_ops.py",
]


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
    """Facade + split topic modules pass strict."""
    result = _run_mypy("--strict", *SPLIT_MODULES)
    file_errors = [
        line
        for line in result.stdout.splitlines()
        if "error:" in line
        and any(
            name in line
            for name in (
                "topic_service.py",
                "topic_helpers.py",
                "topic_lifecycle_service.py",
                "topic_resolve_service.py",
                "topic_action_item_ops.py",
            )
        )
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_topic_split_modules_to_strict():
    """pyproject must keep topic facade + split modules under strict."""
    text = _read(PYPROJECT)
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[.*?"
        r"\"server\.services\.topic_service\".*?"
        r"\"server\.services\.topic_helpers\".*?"
        r"\"server\.services\.topic_lifecycle_service\".*?"
        r"\"server\.services\.topic_resolve_service\".*?"
        r"\"server\.services\.topic_action_item_ops\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "topic_service + its split modules to strict = true"
    )


def test_baseline_mypy_clean_for_topic_service():
    """Sanity: project config mypy stays clean for the facade."""
    result = _run_mypy("server/services/topic_service.py")
    file_errors = [
        line for line in result.stdout.splitlines() if TARGET_BASENAME in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s) in target file:\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_topic_summary_uses_stale_since_kwarg():
    """``TopicSummaryRead(...)`` must use ``stale_since=`` in lifecycle."""
    text = _read(LIFECYCLE)
    assert "stale_since=topic.advance_round_pending_since" in text, (
        "TopicSummaryRead ctor must use stale_since= kwarg (PR5j renamed)."
    )


def test_groups_dict_key_allows_optional_owner():
    """``groups`` key tuple allows nullable ``owner_agent_id``."""
    text = _read(ACTION_OPS)
    assert "dict[tuple[uuid.UUID, uuid.UUID | None], list[TopicActionItem]]" in text, (
        "groups dict must widen its key tuple to allow owner_agent_id = None."
    )


def test_category_uses_enum_ctor():
    """``TopicActionItemRead(...)`` category uses enum ctor."""
    text = _read(ACTION_OPS)
    assert (
        "category=ActionItemCategory(item.category) if item.category else None" in text
    ), (
        "category kwarg must wrap item.category in ActionItemCategory(...) ctor."
    )


def test_audit_entries_uses_any():
    """``audit_entries`` is ``list[dict[str, Any]]`` in resolve."""
    text = _read(RESOLVE)
    assert "audit_entries: list[dict[str, Any]] = []" in text, (
        "audit_entries must be list[dict[str, Any]]."
    )


def test_action_item_helpers_return_typed_dict():
    """Action-item helpers return ``dict[str, Any]``."""
    text = _read(ACTION_OPS)
    assert 'triggered_by: str = "manual",\n) -> dict[str, Any]:' in text or (
        "triggered_by: str = \"manual\",\n) -> dict[str, Any]:" in text
    ), (
        "_complete_action_item_no_commit must return dict[str, Any]."
    )
    assert (
        'triggered_by: str = "deliver",\n    agent_id: uuid.UUID | None = None,\n) -> dict[str, Any]:'
        in text
    ) or (
        "agent_id: uuid.UUID | None = None,\n) -> dict[str, Any]:" in text
        and 'triggered_by: str = "deliver",' in text
    ), (
        "deliver_action_item_no_commit must return dict[str, Any]."
    )


def test_topic_service_facade_reexports_split_modules():
    """Facade must re-export the split public API surfaces."""
    text = _read(TARGET)
    for name in (
        "topic_lifecycle_service",
        "topic_resolve_service",
        "topic_action_item_ops",
        "topic_helpers",
        "topic_comment_service",
    ):
        assert name in text, f"topic_service facade must import/re-export {name}"
