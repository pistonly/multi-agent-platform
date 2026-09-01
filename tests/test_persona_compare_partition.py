"""0db51e10 I4(5a partition): four-way classification of the
``acceptance_status`` partition across personas in ``--persona-compare``.

Plan v2 (a) requires four partition buckets to be unit-tested:

> 3+1=4 种 partition 差异:全一致 / 部分不一致 / 完全不一致 /
> cross-phase 折叠 —— 其他 phase acceptance_status 折叠为
> ``hidden_for_current_persona`` 不报错

These tests exercise the pure-function classifier
``_classify_persona_compare_partition`` over hand-crafted per-persona
``model_dump(mode="json")`` payloads. No HTTP layer / no live experiment
state — the classifier is the only thing under test.
"""

from __future__ import annotations

import pytest

from cli.persona_compare import (
    _PERSONA_COMPARE_PARTITION_ALL_AGREE,
    _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD,
    _PERSONA_COMPARE_PARTITION_FULL_DIFF,
    _PERSONA_COMPARE_PARTITION_PARTIAL_DIFF,
    _classify_persona_compare_partition,
)

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _acceptance(
    *,
    aid: str,
    evidence: bool = False,
    verdict: str | None = None,
) -> dict:
    """Build a minimal AcceptanceStatusRead-shaped payload."""
    return {
        "id": aid,
        "description": f"acceptance {aid}",
        "acceptance_type": "manual",
        "evidence_provided": evidence,
        "reviewer_verdict": verdict,
    }


def _view(
    *,
    acceptance_status: list[dict] | None = None,
    hidden_for_current_persona: bool = False,
) -> dict:
    """Build a per-persona ``ExperimentSummaryRead`` payload.

    Only the fields the classifier reads are populated; everything else
    is left as defaults so the tests stay focused on the partition
    decision.
    """
    return {
        "acceptance_status": acceptance_status if acceptance_status is not None else [],
        "hidden_for_current_persona": hidden_for_current_persona,
    }


# ---------------------------------------------------------------------------
# (a) all_agree — every persona sees the same set
# ---------------------------------------------------------------------------


def test_all_agree_two_personas() -> None:
    views = {
        "host": _view(acceptance_status=[_acceptance(aid="a1", evidence=True)]),
        "reviewer": _view(acceptance_status=[_acceptance(aid="a1", evidence=True)]),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_ALL_AGREE


def test_all_agree_three_personas_empty_acceptance() -> None:
    """All personas see an empty list — also all_agree (no diff)."""
    views = {
        "host": _view(),
        "reviewer": _view(),
        "participant": _view(),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_ALL_AGREE


def test_all_agree_signature_includes_evidence_and_verdict() -> None:
    """Two entries with the same id but different verdict fields are
    distinguished — so the classifier compares semantic equality, not
    just ids."""
    views = {
        "host": _view(
            acceptance_status=[
                _acceptance(aid="a1", evidence=True, verdict="accept"),
            ]
        ),
        "reviewer": _view(
            acceptance_status=[
                _acceptance(aid="a1", evidence=True, verdict="reject"),
            ]
        ),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_FULL_DIFF


# ---------------------------------------------------------------------------
# (a) partial_diff — at least two agree, at least one differs
# ---------------------------------------------------------------------------


def test_partial_diff_two_agree_one_differs() -> None:
    views = {
        "host": _view(
            acceptance_status=[_acceptance(aid="a1"), _acceptance(aid="a2")]
        ),
        "reviewer": _view(
            acceptance_status=[_acceptance(aid="a1"), _acceptance(aid="a2")]
        ),
        "participant": _view(
            acceptance_status=[_acceptance(aid="a1")]
        ),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_PARTIAL_DIFF


def test_partial_diff_subset_relationship() -> None:
    """One persona sees a strict subset of another's set; with a third
    persona matching the superset, the partition is partial_diff
    (host == reviewer but participant differs)."""
    views = {
        "host": _view(
            acceptance_status=[
                _acceptance(aid="a1"),
                _acceptance(aid="a2"),
                _acceptance(aid="a3"),
            ]
        ),
        "reviewer": _view(
            acceptance_status=[
                _acceptance(aid="a1"),
                _acceptance(aid="a2"),
                _acceptance(aid="a3"),
            ]
        ),
        "participant": _view(
            acceptance_status=[_acceptance(aid="a1"), _acceptance(aid="a2")]
        ),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_PARTIAL_DIFF


# ---------------------------------------------------------------------------
# (a) full_diff — every persona's set is distinct
# ---------------------------------------------------------------------------


def test_full_diff_disjoint_sets() -> None:
    views = {
        "host": _view(acceptance_status=[_acceptance(aid="a1")]),
        "reviewer": _view(acceptance_status=[_acceptance(aid="b1")]),
        "participant": _view(acceptance_status=[_acceptance(aid="c1")]),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_FULL_DIFF


def test_full_diff_overlapping_but_distinct() -> None:
    """Pairwise distinct even when some ids are shared, because each
    persona's *set* differs on at least one entry."""
    views = {
        "host": _view(
            acceptance_status=[_acceptance(aid="a1"), _acceptance(aid="a2")]
        ),
        "reviewer": _view(
            acceptance_status=[_acceptance(aid="a1"), _acceptance(aid="b2")]
        ),
        "participant": _view(
            acceptance_status=[_acceptance(aid="c1")]
        ),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_FULL_DIFF


# ---------------------------------------------------------------------------
# (a) cross_phase_fold — at least one persona is hidden
# ---------------------------------------------------------------------------


def test_cross_phase_fold_one_persona_hidden() -> None:
    """Even when acceptance_status would otherwise be all_agree, the
    presence of ``hidden_for_current_persona=True`` on any persona
    promotes the partition to ``cross_phase_fold`` — the diff is
    meaningless because one persona's view is collapsed server-side."""
    views = {
        "host": _view(acceptance_status=[_acceptance(aid="a1")]),
        "reviewer": _view(
            acceptance_status=[_acceptance(aid="a1")],
            hidden_for_current_persona=True,
        ),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD


def test_cross_phase_fold_all_personas_hidden() -> None:
    views = {
        "host": _view(hidden_for_current_persona=True),
        "reviewer": _view(hidden_for_current_persona=True),
        "participant": _view(hidden_for_current_persona=True),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD


def test_cross_phase_fold_takes_precedence_over_full_diff() -> None:
    """If one persona is folded, the partition is reported as a fold
    rather than a diff — even if the other two personas' sets are
    disjoint (the user must fix the phase whitelist before caring
    about the diff)."""
    views = {
        "host": _view(acceptance_status=[_acceptance(aid="a1")]),
        "reviewer": _view(acceptance_status=[_acceptance(aid="b1")]),
        "participant": _view(hidden_for_current_persona=True),
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_views_returns_cross_phase_fold() -> None:
    """Degenerate case: no persona returned a view → safest default is
    to report fold (we cannot compare what we do not have)."""
    assert _classify_persona_compare_partition({}) == _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD


def test_missing_field_treated_as_not_hidden() -> None:
    """A view that omits the ``hidden_for_current_persona`` key is
    treated as not-hidden (False), matching the ExperimentSummaryRead
    schema default. This keeps legacy payloads from being
    mis-classified."""
    views = {
        "host": {"acceptance_status": [_acceptance(aid="a1")]},
        "reviewer": {"acceptance_status": [_acceptance(aid="a1")]},
    }
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_ALL_AGREE


def test_single_persona_is_all_agree() -> None:
    """Trivially, one persona agrees with itself."""
    views = {"host": _view(acceptance_status=[_acceptance(aid="a1")])}
    assert _classify_persona_compare_partition(views) == _PERSONA_COMPARE_PARTITION_ALL_AGREE


@pytest.mark.parametrize(
    "constant_name",
    [
        "_PERSONA_COMPARE_PARTITION_ALL_AGREE",
        "_PERSONA_COMPARE_PARTITION_PARTIAL_DIFF",
        "_PERSONA_COMPARE_PARTITION_FULL_DIFF",
        "_PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD",
    ],
)
def test_constants_are_exposed_in_persona_compare_module(constant_name: str) -> None:
    """Sanity: every classifier label is exported from ``cli.persona_compare``
    (T43 起的权威定义处，原 cli.main re-export 已随死代码清理移除) so
    downstream callers (audit writer, web UI summary cards) can
    reference the same string."""
    import cli.persona_compare as persona_compare_module

    value = getattr(persona_compare_module, constant_name)
    assert isinstance(value, str)
    # And the classifier actually returns one of these literal strings.
    assert value in {
        _PERSONA_COMPARE_PARTITION_ALL_AGREE,
        _PERSONA_COMPARE_PARTITION_PARTIAL_DIFF,
        _PERSONA_COMPARE_PARTITION_FULL_DIFF,
        _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD,
    }
