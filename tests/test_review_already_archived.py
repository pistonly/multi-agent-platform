"""archived review cannot be resolved / withdrawn (experiment 18f1d8f6 I1(d)).

Pins plan (d) acceptance:

1. ``update_review_item`` raises ``StateTransitionError`` with
   ``error_code='REVIEW_ALREADY_ARCHIVED'`` when the parent review
   carries ``archived_at != NULL``.
2. ``withdraw_review`` raises the same structured subcode on archived
   rows.
3. The exception's ``hint`` points the operator at
   ``--include-archived --plan-version <v>`` to inspect history and
   instructs them to file a new review for the new plan_version.
4. The subcode is registered in the SDK ``STATE_MACHINE_ERROR_CODES``
   registry + ``RECOVERY_HINTS`` table + YAML fixture, mirroring the
   156468d0 state_machine_error template pattern.
5. The error code is exposed via the HTTP layer (the
   ``StateTransitionError`` JSON envelope already routes ``error_code`` /
   ``hint`` to the response body).
6. Active reviews (archived_at=NULL) are unaffected by the guard.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from map_client.errors import (
    RECOVERY_HINTS,
    STATE_MACHINE_ERROR_CODES,
    STATE_MACHINE_REVIEW_ALREADY_ARCHIVED,
)
from map_types import ReviewArchivedReason
from map_types.enums import (
    ExperimentPhase,
    ReviewItemKind,
    ReviewItemStatus,
    ReviewSubstituteKind,
)
from sqlalchemy.orm import Session

from tests._frontmatter import make_valid_plan

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    Project,
    Review,
    ReviewItem,
)
from server.services.errors import StateTransitionError
from server.services.review_service import (
    _ensure_review_not_archived,
    update_review_item,
    withdraw_review,
)


def _make_project(db: Session) -> Project:
    p = Project(
        id=uuid.uuid4(),
        project_key="t",
        name="T",
        workspace_path="/tmp/t",
    )
    db.add(p)
    db.flush()
    return p


def _make_agent(
    db: Session,
    *,
    project_id: uuid.UUID,
    name: str = "a",
    role: AgentRole = AgentRole.agent,
) -> Agent:
    a = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="x",
        api_token_prefix="x",
        role=role,
    )
    db.add(a)
    db.flush()
    return a


def _make_experiment(
    db: Session,
    *,
    project_id: uuid.UUID,
    creator_id: uuid.UUID,
    phase: ExperimentPhase = ExperimentPhase.review,
) -> Experiment:
    e = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="t",
        phase=phase,
        current_plan_version=1,
    )
    db.add(e)
    db.flush()
    return e


def _make_review(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    archived_at: datetime | None = None,
    archived_reason: ReviewArchivedReason | None = None,
) -> Review:
    r = Review(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        reviewer_agent_id=reviewer_id,
        plan_version=1,
        substitute_kind=ReviewSubstituteKind.none,
        archived_at=archived_at,
        archived_reason=archived_reason,
    )
    db.add(r)
    db.flush()
    return r


def _make_item(
    db: Session,
    *,
    review_id: uuid.UUID,
    status: ReviewItemStatus = ReviewItemStatus.open,
) -> ReviewItem:
    item = ReviewItem(
        id=uuid.uuid4(),
        review_id=review_id,
        kind=ReviewItemKind.unreasonable,
        content="needs resolution",
        status=status,
    )
    db.add(item)
    db.flush()
    return item


# ---------------------------------------------------------------------------
# Service-layer guard
# ---------------------------------------------------------------------------


def test_ensure_review_not_archived_raises_for_archived(db_session):
    """Archived review triggers REVIEW_ALREADY_ARCHIVED with structured hint."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    archived = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        archived_at=datetime.utcnow(),
        archived_reason=ReviewArchivedReason.auto,
    )

    with pytest.raises(StateTransitionError) as exc_info:
        _ensure_review_not_archived(archived)
    assert exc_info.value.error_code == "REVIEW_ALREADY_ARCHIVED"
    assert exc_info.value.hint is not None
    assert "--include-archived" in exc_info.value.hint


def test_ensure_review_not_archived_passes_for_active(db_session):
    """Active review (archived_at=NULL) does not raise."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    active = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        archived_at=None,
    )
    # No exception expected.
    _ensure_review_not_archived(active)


def test_update_review_item_blocks_archived_review(db_session):
    """PATCH /review-items/{id} returns 409 + REVIEW_ALREADY_ARCHIVED."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    archived = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        archived_at=datetime.utcnow(),
        archived_reason=ReviewArchivedReason.auto,
    )
    item = _make_item(db_session, review_id=archived.id)
    db_session.commit()

    from map_types import ReviewItemUpdate

    with pytest.raises(StateTransitionError) as exc_info:
        update_review_item(
            db_session,
            item_id=item.id,
            actor=creator,
            payload=ReviewItemUpdate(status=ReviewItemStatus.resolved),
        )
    assert exc_info.value.error_code == "REVIEW_ALREADY_ARCHIVED"


def test_update_review_item_succeeds_on_active_review(db_session):
    """Active review (not archived) is unaffected by the guard."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    active = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        archived_at=None,
    )
    item = _make_item(db_session, review_id=active.id)
    db_session.commit()

    from map_types import ReviewItemUpdate

    # The state machine may still reject (open → resolved is not a valid
    # transition without via_plan_revision), but the failure must NOT be
    # REVIEW_ALREADY_ARCHIVED.
    with pytest.raises(StateTransitionError) as exc_info:
        update_review_item(
            db_session,
            item_id=item.id,
            actor=creator,
            payload=ReviewItemUpdate(status=ReviewItemStatus.resolved),
        )
    assert exc_info.value.error_code != "REVIEW_ALREADY_ARCHIVED"


def test_withdraw_review_blocks_archived_review(db_session):
    """withdraw_review on archived review triggers REVIEW_ALREADY_ARCHIVED."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
    )
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    archived = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        archived_at=datetime.utcnow(),
        archived_reason=ReviewArchivedReason.manual,
    )
    db_session.commit()

    with pytest.raises(StateTransitionError) as exc_info:
        withdraw_review(
            db_session,
            experiment_id=exp.id,
            review_id=archived.id,
            actor=reviewer,
        )
    assert exc_info.value.error_code == "REVIEW_ALREADY_ARCHIVED"


def test_archived_reason_in_error_message(db_session):
    """The error message surfaces archived_reason so the user knows why."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    archived = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        archived_at=datetime.utcnow(),
        archived_reason=ReviewArchivedReason.superseded,
    )

    with pytest.raises(StateTransitionError) as exc_info:
        _ensure_review_not_archived(archived)
    assert "reason=superseded" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SDK registry + fixture round-trip
# ---------------------------------------------------------------------------


def test_sdk_registry_includes_review_already_archived():
    """STATE_MACHINE_ERROR_CODES includes the new subcode."""
    assert STATE_MACHINE_REVIEW_ALREADY_ARCHIVED in STATE_MACHINE_ERROR_CODES


def test_sdk_recovery_hint_points_at_include_archived_flag():
    """The SDK recovery hint for the new code references --include-archived."""
    hint = RECOVERY_HINTS[STATE_MACHINE_REVIEW_ALREADY_ARCHIVED]
    assert isinstance(hint, str)
    assert "--include-archived" in hint
    assert "plan_version" in hint


def test_sdk_recovery_hint_helper_returns_string():
    """recovery_hint(STATE_MACHINE_REVIEW_ALREADY_ARCHIVED) returns the hint."""
    from map_client.errors import recovery_hint

    hint = recovery_hint(STATE_MACHINE_REVIEW_ALREADY_ARCHIVED)
    assert hint == RECOVERY_HINTS[STATE_MACHINE_REVIEW_ALREADY_ARCHIVED]


# ---------------------------------------------------------------------------
# HTTP layer envelope
# ---------------------------------------------------------------------------


def test_api_update_review_item_returns_409_with_subcode(
    client, admin_headers, auth_headers, reviewer, project, db_session
):
    """End-to-end: archived review → 409 + error_code='REVIEW_ALREADY_ARCHIVED'."""
    from map_types.enums import ReviewArchivedReason

    # Use the host's auth_headers (creator) to create the experiment.
    exp_resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "archived resolve test",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    )
    assert exp_resp.status_code == 201, exp_resp.text
    exp_id = exp_resp.json()["id"]

    review_resp = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["archived-review-test item"]},
    )
    assert review_resp.status_code == 201, review_resp.text
    review_id = review_resp.json()["id"]
    item_id = review_resp.json()["items"][0]["id"]

    # Mark the review archived via the test's session. In production,
    # plan_revise auto-archive is the canonical trigger; this test pins
    # the *guard* contract, not the trigger.
    from server.domain.models import Review as ReviewModel

    row = db_session.get(ReviewModel, uuid.UUID(review_id))
    row.archived_at = datetime.utcnow()
    row.archived_reason = ReviewArchivedReason.auto
    db_session.commit()

    # Now PATCH the item — must surface REVIEW_ALREADY_ARCHIVED. The
    # server-side StateTransitionError → 422 (per register_domain_exception_handlers).
    patch_resp = client.patch(
        f"/api/v1/review-items/{item_id}",
        json={"status": "resolved"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 422, patch_resp.text
    body = patch_resp.json()
    assert body.get("error_code") == "REVIEW_ALREADY_ARCHIVED"
    assert body.get("hint") is not None
    assert "--include-archived" in body["hint"]
