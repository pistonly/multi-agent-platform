"""phase_owner resolver + informational_only auto-classification
(experiment f873c287 I1(b) + I1(a) acceptance).

Covers the resolver's per-phase owner table, the ``is_informational_only``
predicate (I1(a)), and the e2e flow where ``phase_owner`` mirrors the
phase on every transition. The I1(b) acceptance — "(b) experiment 表
phase_owner 字段 + migration + 单测覆盖 6 phase 默认值"
—— is checked here.
"""

from __future__ import annotations

from map_types.enums import ExperimentPhase, PhaseOwner

from server.services.phase_owner_resolver import is_informational_only, owner_for
from tests._frontmatter import make_valid_plan

# ---------------------------------------------------------------------------
# I1(b): per-phase owner table (covers all 7 ExperimentPhase values + the
# logical "revise" sub-phase).
# ---------------------------------------------------------------------------

def test_phase_owner_table_covers_all_seven_phases():
    expected = {
        ExperimentPhase.draft: PhaseOwner.host,
        ExperimentPhase.review: PhaseOwner.reviewer,
        ExperimentPhase.approved: PhaseOwner.host,
        ExperimentPhase.running: PhaseOwner.host,
        ExperimentPhase.result_review: PhaseOwner.reviewer,
        ExperimentPhase.done: PhaseOwner.host,
        ExperimentPhase.cancelled: PhaseOwner.host,
    }
    for phase, owner in expected.items():
        assert owner_for(phase) is owner, (
            f"phase={phase.value} expected owner={owner.value} "
            f"got={owner_for(phase).value}"
        )


def test_phase_owner_revise_subphase_is_host():
    """``revise`` is a logical sub-phase of review; the host owns the
    plan_revise decision, so the resolver must answer ``host``."""
    assert owner_for("revise") is PhaseOwner.host


def test_owner_for_string_and_enum_match():
    for phase in ExperimentPhase:
        assert owner_for(phase) is owner_for(phase.value)


def test_owner_for_unknown_phase_defaults_to_host():
    """Defensive default: an unknown phase must NOT silently demote an
    experiment to informational_only; the resolver falls back to host so
    it stays actionable."""
    assert owner_for("not_a_real_phase") is PhaseOwner.host


# ---------------------------------------------------------------------------
# I1(a): informational_only auto-classification (3-way AND).
# ---------------------------------------------------------------------------

def test_informational_only_true_only_when_all_three_conditions():
    """The AND predicate: actions == [] AND blocked_on != None AND
    phase_owner != host."""
    # All three: True
    assert (
        is_informational_only(
            ExperimentPhase.review,
            actions=[],
            blocked_on="awaiting_non_creator_review",
        )
        is True
    )
    assert (
        is_informational_only(
            ExperimentPhase.result_review,
            actions=[],
            blocked_on="awaiting_result_approval",
        )
        is True
    )


def test_informational_only_false_when_actions_present():
    """An experiment with actions is never informational."""
    assert (
        is_informational_only(
            ExperimentPhase.review,
            actions=["plan_revise"],
            blocked_on="open_unreasonable_item",
        )
        is False
    )


def test_informational_only_false_when_blocked_on_empty():
    """No blocked_on → experiment is fully actionable → not informational."""
    assert (
        is_informational_only(
            ExperimentPhase.approved,
            actions=[],
            blocked_on="none",
        )
        is False
    )
    # explicit None
    assert (
        is_informational_only(
            ExperimentPhase.approved,
            actions=[],
            blocked_on=None,
        )
        is False
    )


def test_informational_only_false_when_phase_owner_is_host():
    """Even if blocked, a host-owned phase is NOT informational —
    the host can advance by themselves (e.g. start, complete)."""
    assert (
        is_informational_only(
            ExperimentPhase.approved,
            actions=[],
            blocked_on="some_block",  # unusual but defensive
        )
        is False
    )
    assert (
        is_informational_only(
            ExperimentPhase.running,
            actions=[],
            blocked_on="some_block",
        )
        is False
    )


# ---------------------------------------------------------------------------
# I1(b) e2e: every phase transition syncs phase_owner through phase_service.
# ---------------------------------------------------------------------------


def test_phase_service_syncs_phase_owner_on_each_transition(
    client, auth_headers, reviewer, project
):
    """End-to-end: draft → review → approved → running → result_review → done
    should each sync the phase_owner column to the resolver's answer.
    """
    from map_types.enums import ExperimentPhase as SdkPhase

    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "phase_owner sync 实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    # After create + submit_for_review: phase=review, phase_owner=reviewer
    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["phase"] == SdkPhase.review.value
    assert detail["phase_owner"] == PhaseOwner.reviewer.value
    assert detail["informational_only"] is True

    # Reviewer submits a review + closes items; host approves.
    client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    )

    approve = client.post(
        f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers
    )
    assert approve.status_code == 200, approve.text

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["phase"] == SdkPhase.approved.value
    assert detail["phase_owner"] == PhaseOwner.host.value
    assert detail["informational_only"] is False  # blocked_on == "none"

    # Start: phase=running, owner=host
    start = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    assert start.status_code == 200
    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["phase"] == SdkPhase.running.value
    assert detail["phase_owner"] == PhaseOwner.host.value

    # Complete: phase=result_review, owner=reviewer, blocked_on != None
    complete = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={
            "summary": "实验完成",
            "content_md": "结果",
            "metadata": {"pytest_summary": "ok"},
        },
    )
    assert complete.status_code == 200, complete.text
    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["phase"] == SdkPhase.result_review.value
    assert detail["phase_owner"] == PhaseOwner.reviewer.value
    assert detail["informational_only"] is True

    # Reviewer accepts result: phase=done, owner=host
    accept = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json={"summary": "通过", "content_md": "result 评审通过"},
    )
    assert accept.status_code == 200, accept.text
    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=reviewer["headers"]).json()
    assert detail["phase"] == SdkPhase.done.value
    assert detail["phase_owner"] == PhaseOwner.host.value


def test_informational_only_flag_only_true_during_reviewer_wait(
    client, auth_headers, reviewer, project
):
    """A draft experiment (host owns) and an approved experiment (host owns,
    blocked_on == "none") must both have ``informational_only == False``;
    only the phases where the host is structurally blocked by another
    persona should flip it True."""
    # --- draft: phase=draft, owner=host → NOT informational
    draft = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "draft 实验", "plan": {"content_md": make_valid_plan(body="## 计划")}},
    ).json()
    detail = client.get(f"/api/v1/experiments/{draft['id']}", headers=auth_headers).json()
    assert detail["phase"] == "draft"
    assert detail["phase_owner"] == "host"
    assert detail["informational_only"] is False

    # --- review (no reviewer yet): blocked_on=awaiting_non_creator_review → informational
    review = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "review 实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    detail = client.get(f"/api/v1/experiments/{review['id']}", headers=auth_headers).json()
    assert detail["phase"] == "review"
    assert detail["phase_owner"] == "reviewer"
    assert detail["blocked_on"] == "awaiting_non_creator_review"
    assert detail["informational_only"] is True
