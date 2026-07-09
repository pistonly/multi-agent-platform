"""Tests for eng experiment (55634575) PR5e — promote
server/services/todo_service.py to mypy strict.

Fifth services-layer module on the strict surface (after
project_service / phase_service / experiment_capabilities_service /
mention_service).

Pre-existing gaps fixed (4 total):

* Lines 54 + 68 — ``work_items: list | None`` →
  ``list[TopicWorkItem] | None``. ``TopicWorkItem`` added to the
  ``TYPE_CHECKING`` import block.
* Line 102 — kwarg rename ``advance_round_pending_since=`` →
  ``stale_since=`` to match the schema's field name (the schema still
  accepts the old name as a ``validation_alias`` for back-compat,
  but mypy checks the field name). The Topic ORM column
  ``topic.advance_round_pending_since`` keeps its name for now (DB
  migration deferred).
* Line 370 — ``status=item.status`` (typed ``ReviewItemStatus | None``
  via the ORM column) → ``status=cast(ReviewItemStatus, item.status)``.
  The pre-existing WHERE filter
  ``ReviewItem.status.in_(_REPLY_STATES)`` already guarantees
  non-None at this point; ``cast`` documents the post-WHERE
  invariant for mypy without inserting an extra branch.

Pins:
1. ``server/services/todo_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 9 modules in one override block.
3. Baseline mypy (project config) clean.
4. Regression guards: no bare ``list`` annotations; the kwarg
   rename is permanent.
"""

from __future__ import annotations

import re
import subprocess
import sys

PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"
PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"


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


def test_todo_service_passes_mypy_strict():
    """``server/services/todo_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/todo_service.py")
    assert result.returncode == 0, (
        f"mypy --strict server/services/todo_service.py failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_nine_to_strict():
    """pyproject must list all 9 modules in one override block."""
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
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 9 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_todo_service():
    """Sanity: with the pyproject config (which promotes todo_service
    to strict), running mypy on it alone is still clean.
    """
    result = _run_mypy("server/services/todo_service.py")
    assert result.returncode == 0, (
        f"baseline mypy server/services/todo_service.py failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_todo_service_no_bare_list_annotations():
    """Regression guard: no bare ``list`` annotations in the file
    (each must be ``list[<Typed>]`` or ``Sequence[<Typed>]``).
    """
    path = f"{PROJECT_ROOT}/server/services/todo_service.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    no_comments = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    # Allow ``list,`` / ``list)`` for multi-param signatures, but not
    # ``list | None`` (the PR5e regression pattern).
    bare = re.findall(r":\s*list(?![a-zA-Z0-9_\[\|])", no_comments)
    assert not bare, (
        f"server/services/todo_service.py must not use bare ``list`` "
        f"annotations. Found {len(bare)} occurrences."
    )


def test_todo_service_uses_stale_since_kwarg():
    """Regression guard for the PR5e kwarg rename: the deprecated
    ``advance_round_pending_since=`` kwarg name must not be used
    in the PendingAdvanceRoundTodoRead construction site (line 102).

    Note: the Topic ORM column ``topic.advance_round_pending_since``
    keeps its name until a future DB migration renames it. Only the
    **schema kwarg** is renamed.
    """
    path = f"{PROJECT_ROOT}/server/services/todo_service.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # The bad pattern is passing the kwarg to the schema ctor:
    assert "PendingAdvanceRoundTodoRead(\n" in text, (
        "PR5e expected the PendingAdvanceRoundTodoRead ctor to remain "
        "multi-line"
    )
    # Extract the ctor block and check it does not use the old name.
    block = re.search(
        r"PendingAdvanceRoundTodoRead\(\n(.*?)\n        \)",
        text,
        re.DOTALL,
    )
    assert block is not None, (
        "Could not locate PendingAdvanceRoundTodoRead ctor block"
    )
    assert "advance_round_pending_since=" not in block.group(1), (
        "PendingAdvanceRoundTodoRead ctor must use ``stale_since=`` "
        "(the schema field name), not the deprecated alias."
    )
    assert "stale_since=" in block.group(1), (
        "PendingAdvanceRoundTodoRead ctor must set ``stale_since=`` "
        "(verified via grep)."
    )
