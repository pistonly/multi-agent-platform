"""plan_revise auto-archive trigger (experiment 18f1d8f6 I1(b)).

Pins plan (b) acceptance:

1. ``plan_revise`` archives every review whose ``plan_version`` is less
   than the new ``current_plan_version`` with
   ``archived_reason='auto' + archived_at=now()``.
2. The cascade is idempotent: re-running the trigger (e.g. another
   ``plan_revise`` that bumps to v3 then v4) does NOT overwrite
   ``archived_at`` on rows already archived by an earlier bump.
3. Reviews whose ``plan_version == new_version`` (the current one) stay
   active even if the host has just queued a future bump.
4. The early-return branch in ``revise_plan`` (content unchanged + no
   addressed items) does NOT trigger archive, because no version bump
   happens.
5. Manually-archived rows (``archived_reason='manual'``) are preserved
   — the cascade only touches rows with ``archived_at IS NULL``.
6. Reviews from a *different* experiment are not affected.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from map_types import PlanRevise
from map_types.enums import (
    AgentRole,
    ExperimentPhase,
    ReviewArchivedReason,
    ReviewSubstituteKind,
)
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    Experiment,
    PlanVersion,
    Project,
    Review,
)
from server.services.plan_service import revise_plan


def _make_project(db: Session, *, key: str = "test-proj") -> Project:
    project = Project(
        id=uuid.uuid4(),
        project_key=key,
        name="Test Project",
        workspace_path="/tmp/test",
    )
    db.add(project)
    db.flush()
    return project


def _make_agent(
    db: Session,
    *,
    project_id: uuid.UUID,
    name: str = "agent",
    role: AgentRole = AgentRole.agent,
) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="x",
        api_token_prefix="x",
        role=role,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_experiment(
    db: Session,
    *,
    project_id: uuid.UUID,
    creator_id: uuid.UUID,
    phase: ExperimentPhase = ExperimentPhase.review,
    current_plan_version: int = 1,
) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="plan revise auto archive test",
        phase=phase,
        current_plan_version=current_plan_version,
    )
    db.add(exp)
    db.flush()
    return exp


def _make_plan_version(
    db: Session, *, experiment_id: uuid.UUID, author_id: uuid.UUID, version: int, content: str
) -> PlanVersion:
    pv = PlanVersion(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        version=version,
        content_md=content,
        author_agent_id=author_id,
    )
    db.add(pv)
    db.flush()
    return pv


def _make_review(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    plan_version: int,
    archived_reason: ReviewArchivedReason | None = None,
    archived_at: datetime | None = None,
) -> Review:
    review = Review(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        reviewer_agent_id=reviewer_id,
        plan_version=plan_version,
        substitute_kind=ReviewSubstituteKind.none,
        archived_at=archived_at,
        archived_reason=archived_reason,
    )
    db.add(review)
    db.flush()
    return review


# ---------------------------------------------------------------------------
# I1(b) acceptance: plan_revise archives prior-version reviews
# ---------------------------------------------------------------------------


def test_plan_revise_archives_prior_version_reviews(db_session):
    """plan_revise → current_plan_version++ → old reviews archived with reason='auto'."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
        current_plan_version=1,
    )
    _make_plan_version(
        db_session,
        experiment_id=exp.id,
        author_id=creator.id,
        version=1,
        content="v1 plan",
    )

    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    v1_review = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    db_session.commit()

    new_plan = revise_plan(
        db_session,
        experiment_id=exp.id,
        author=creator,
        payload=PlanRevise(content_md="v2 plan revised", change_note="address v1 items"),
    )
    assert new_plan.version == 2

    db_session.refresh(v1_review)
    assert v1_review.archived_at is not None
    assert v1_review.archived_reason == ReviewArchivedReason.auto
    delta = datetime.utcnow() - v1_review.archived_at.replace(tzinfo=None)
    assert delta < timedelta(minutes=1)


def test_plan_revise_does_not_archive_current_version_review(db_session):
    """A review at the *current* plan_version stays active; bumping archives it.

    The current (v2) review is preserved while v2 is current, but as soon
    as the host bumps to v3 the v2 review is no longer canonical and
    gets archived alongside v1. Only reviews whose ``plan_version`` is
    strictly less than the *new* ``current_plan_version`` are archived;
    reviews at exactly ``new_version`` (e.g. a fresh review submitted at
    v2 before the bump) stay active until the *next* bump.
    """
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
        current_plan_version=2,
    )
    _make_plan_version(
        db_session, experiment_id=exp.id, author_id=creator.id, version=1, content="v1 plan"
    )
    _make_plan_version(
        db_session, experiment_id=exp.id, author_id=creator.id, version=2, content="v2 plan"
    )

    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    v1_review = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    v3_review = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=3,
    )
    db_session.commit()

    revise_plan(
        db_session,
        experiment_id=exp.id,
        author=creator,
        payload=PlanRevise(content_md="v3 plan revised", change_note="another revise"),
    )
    db_session.refresh(v1_review)
    db_session.refresh(v3_review)
    assert v1_review.archived_at is not None
    assert v1_review.archived_reason == ReviewArchivedReason.auto
    # v3 review (at the new current) stays active — it is the canonical
    # review for the new plan version.
    assert v3_review.archived_at is None
    assert v3_review.archived_reason is None


def test_plan_revise_archive_is_idempotent_on_existing_archives(db_session):
    """A pre-archived row keeps its original archived_at — not overwritten."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
        current_plan_version=1,
    )
    _make_plan_version(
        db_session, experiment_id=exp.id, author_id=creator.id, version=1, content="v1 plan"
    )

    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    manual_archive_time = datetime.utcnow() - timedelta(days=7)
    v1_review_manual = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
        archived_at=manual_archive_time,
        archived_reason=ReviewArchivedReason.manual,
    )
    db_session.commit()

    revise_plan(
        db_session,
        experiment_id=exp.id,
        author=creator,
        payload=PlanRevise(content_md="v2 plan revised", change_note="bump"),
    )
    db_session.refresh(v1_review_manual)
    # Manual archive metadata is preserved.
    assert v1_review_manual.archived_at == manual_archive_time
    assert v1_review_manual.archived_reason == ReviewArchivedReason.manual


def test_plan_revise_no_archive_when_content_unchanged(db_session):
    """The early-return branch (content identical + no items) does not archive.

    This branch never increments ``current_plan_version`` so no cascade
    should fire. Pin the contract so a future refactor doesn't add a
    spurious archive call here.
    """
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
        current_plan_version=1,
    )
    _make_plan_version(
        db_session, experiment_id=exp.id, author_id=creator.id, version=1, content="same plan"
    )

    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    v1_review = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    db_session.commit()

    returned = revise_plan(
        db_session,
        experiment_id=exp.id,
        author=creator,
        payload=PlanRevise(content_md="same plan", change_note=""),
    )
    assert returned.version == 1  # early-return: no bump
    db_session.refresh(v1_review)
    assert v1_review.archived_at is None
    assert v1_review.archived_reason is None


def test_plan_revise_archive_only_affects_target_experiment(db_session):
    """Reviews from a different experiment are untouched."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp_a = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
        current_plan_version=1,
    )
    exp_b = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
        current_plan_version=1,
    )
    _make_plan_version(
        db_session, experiment_id=exp_a.id, author_id=creator.id, version=1, content="A v1"
    )
    _make_plan_version(
        db_session, experiment_id=exp_b.id, author_id=creator.id, version=1, content="B v1"
    )

    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    a_review = _make_review(
        db_session,
        experiment_id=exp_a.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    b_review = _make_review(
        db_session,
        experiment_id=exp_b.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    db_session.commit()

    revise_plan(
        db_session,
        experiment_id=exp_a.id,
        author=creator,
        payload=PlanRevise(content_md="A v2", change_note="bump A"),
    )
    db_session.refresh(a_review)
    db_session.refresh(b_review)
    assert a_review.archived_at is not None
    assert a_review.archived_reason == ReviewArchivedReason.auto
    # Experiment B is untouched.
    assert b_review.archived_at is None
    assert b_review.archived_reason is None


def test_plan_revise_repeated_bumps_do_not_overwrite_archive_at(db_session):
    """Bumping v2 → v3 → v4 must not overwrite the original archived_at on v1 reviews."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        phase=ExperimentPhase.review,
        current_plan_version=1,
    )
    _make_plan_version(
        db_session, experiment_id=exp.id, author_id=creator.id, version=1, content="v1 plan"
    )

    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")
    v1_review = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    db_session.commit()

    revise_plan(
        db_session,
        experiment_id=exp.id,
        author=creator,
        payload=PlanRevise(content_md="v2 plan", change_note="bump"),
    )
    db_session.refresh(v1_review)
    first_archive_at = v1_review.archived_at
    assert first_archive_at is not None

    # Second bump — v1 review is already archived; archived_at must NOT change.
    revise_plan(
        db_session,
        experiment_id=exp.id,
        author=creator,
        payload=PlanRevise(content_md="v3 plan", change_note="bump again"),
    )
    db_session.refresh(v1_review)
    assert v1_review.archived_at == first_archive_at
    assert v1_review.archived_reason == ReviewArchivedReason.auto
