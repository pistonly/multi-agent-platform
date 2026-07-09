"""Tests for eng experiment (55634575) PR5g — promote
server/services/agent_work_service.py to mypy strict.

Eleventh strict module (4 API + 7 services).

Pre-existing gaps fixed (7 total):

* Lines 47-66 — ``_make_item`` / ``_bucket`` typed ``kind: str`` where
  the schema field expects ``SummaryBucketKind`` (Literal of 6 kind
  strings). Fixed by typing as ``SummaryBucketKind`` and importing the
  alias from ``server.domain.schemas``. The dict
  ``_BUCKET_KIND_VISIBILITY`` now carries an explicit
  ``dict[SummaryBucketKind, BucketVisibility]`` annotation.
* Line 161 — ``bucket_items`` typed as ``dict[str, list[...]]`` so all
  loop iterations inferred ``kind: str``; tightened to
  ``dict[SummaryBucketKind, list[SummaryBucketItem]]`` so the persona
  filter and bucket list-comprehension see the literal type.
* Line 216 — loop variable ``r`` was previously inferred from the
  ``pending_round_acks`` loop above (PendingRoundAckTodoRead). Renamed
  to ``reply`` so the new iterable (``pending_topic_replies``,
  PendingTopicReplyTodoRead) gets a fresh, accurate type.
* Line 276 — same pattern: ``p`` reused across ``pending_advance_rounds``
  (PendingAdvanceRoundTodoRead) and ``pending_replies``
  (PendingReplyRead). Renamed second loop variable to ``pending``.
* Line 298 — same pattern: ``e`` reused across ``pending_result_reviews``
  (ExperimentSummaryRead) and ``experiment_review_informational``
  (ExperimentReviewInformationalRead). Renamed second loop variable
  to ``informational``.

Pins:
1. ``server/services/agent_work_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 11 modules in one override block.
3. Baseline mypy (project config) clean.
4. ``_make_item`` and ``_bucket`` accept ``SummaryBucketKind``, not bare str.
5. ``_BUCKET_KIND_VISIBILITY`` and ``bucket_items`` are typed as
   ``dict[SummaryBucketKind, ...]`` (no bare ``dict[str, ...]``).
6. The 3 reused loop variables are renamed (no ``r``, ``p``, or ``e``
   referencing two different types in adjacent loops).
"""

from __future__ import annotations

import re
import subprocess
import sys

PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"
PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"
AGENT_WORK = f"{PROJECT_ROOT}/server/services/agent_work_service.py"


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


def test_agent_work_service_passes_mypy_strict():
    """``server/services/agent_work_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/agent_work_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "agent_work_service.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"mypy --strict found {len(file_errors)} error(s) in "
        f"server/services/agent_work_service.py:\n" + "\n".join(file_errors)
        + f"\n\nfull output:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_eleven_to_strict():
    """pyproject must list all 11 modules in one override block."""
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
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 11 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_agent_work_service():
    """Sanity: with the pyproject config (which promotes agent_work_service
    to strict), running mypy on it alone is still clean.
    """
    result = _run_mypy("server/services/agent_work_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "agent_work_service.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"baseline mypy found {len(file_errors)} error(s) in "
        f"server/services/agent_work_service.py:\n" + "\n".join(file_errors)
    )


def test_make_item_and_bucket_typed_as_summary_bucket_kind():
    """Regression guard: ``_make_item`` and ``_bucket`` must accept
    ``SummaryBucketKind``, not bare ``str``. The schema field
    ``kind: SummaryBucketKind`` is a Literal alias and bare ``str``
    widens the type to anything.
    """
    text = _read(AGENT_WORK)
    assert "def _make_item(*, kind: SummaryBucketKind" in text, (
        "_make_item must type kind as SummaryBucketKind (PR5g typed "
        "helper signature to match the schema's Literal alias)"
    )
    assert "def _bucket(\n    *,\n    kind: SummaryBucketKind" in text or (
        "def _bucket(\n    *,\n    kind: SummaryBucketKind," in text
    ) or "def _bucket(*, kind: SummaryBucketKind," in text or (
        "kind: SummaryBucketKind," in text
        and "def _bucket(" in text
    ), (
        "_bucket must type kind as SummaryBucketKind (PR5g typed helper "
        "signature to match the schema's Literal alias)"
    )


def test_bucket_visibility_dict_is_typed():
    """Regression guard: ``_BUCKET_KIND_VISIBILITY`` must be
    ``dict[SummaryBucketKind, BucketVisibility]`` (no bare ``dict``).
    """
    text = _read(AGENT_WORK)
    assert "_BUCKET_KIND_VISIBILITY: dict[SummaryBucketKind, BucketVisibility]" in text, (
        "_BUCKET_KIND_VISIBILITY must be annotated "
        "dict[SummaryBucketKind, BucketVisibility] so the indexed read "
        "yields a BucketVisibility literal, not Any."
    )


def test_bucket_items_dict_uses_summary_bucket_kind():
    """Regression guard: ``bucket_items`` must be
    ``dict[SummaryBucketKind, list[SummaryBucketItem]]``, not bare
    ``dict[str, ...]`` — otherwise every loop re-infers ``str`` and the
    strict surface regresses to pre-PR5g typing.
    """
    text = _read(AGENT_WORK)
    assert (
        "bucket_items: dict[SummaryBucketKind, list[SummaryBucketItem]]"
        in text
    ), (
        "bucket_items must be typed "
        "dict[SummaryBucketKind, list[SummaryBucketItem]] (PR5g tightens "
        "from dict[str, ...] so iteration yields the literal kind)."
    )


def test_loop_variables_renamed_to_avoid_type_reuse():
    """Regression guard: the three reused loop variables must be
    renamed — PR5g detected three [assignment] errors where ``r``, ``p``,
    and ``e`` were inferred from a previous loop with a different
    iterable type. The fix renames the second loop to use distinct
    variable names so mypy infers each from its own iterable.

    Specifically:

    * ``for reply in todos.pending_topic_replies:``
    * ``for pending in todos.pending_replies:``
    * ``for informational in todos.experiment_review_informational:``
    """
    text = _read(AGENT_WORK)
    assert "for reply in todos.pending_topic_replies:" in text, (
        "second loop over pending_topic_replies must be renamed to "
        "'reply' so mypy infers PendingTopicReplyTodoRead, not "
        "PendingRoundAckTodoRead from the previous loop."
    )
    assert "for pending in todos.pending_replies:" in text, (
        "loop over pending_replies must be renamed to 'pending' so mypy "
        "infers PendingReplyRead, not PendingAdvanceRoundTodoRead."
    )
    assert "for informational in todos.experiment_review_informational:" in text, (
        "loop over experiment_review_informational must be renamed to "
        "'informational' so mypy infers "
        "ExperimentReviewInformationalRead, not ExperimentSummaryRead."
    )
