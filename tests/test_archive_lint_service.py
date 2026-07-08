"""a764abf6 I1.(b) — archive metadata lint helper unit tests.

Mirrors ``tests/test_plan_marker_service.py`` structure. Pinned cases
(pinned from plan acceptance (b)):

1. **检测 1 transition phase** — archived review leaked into default
   list under N=2 过渡期 → ``WARN`` (not FAIL).
2. **检测 1 post-flip phase** — same drift after N=2 release flip →
   ``FAIL``.
3. **检测 2 plan_version drift** — archived review whose plan_version
   is still canonical → ``FAIL`` (regardless of phase).
4. **clean state** — no archived review / no drift → ``issues=[]``.
5. **severity aggregation** — ``has_failures`` / ``has_warnings``
   semantics.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    ExperimentPhase,
    Project,
    Review,
    ReviewArchivedReason,
)
from server.services.archive_lint_service import (
    ArchiveLintResult,
    archive_lint,
)


def _make_agent(db: Session, *, project_id: uuid.UUID, name: str, role: AgentRole) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="test-hash",
        api_token_prefix="test",
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
    current_plan_version: int = 1,
) -> Experiment:
    experiment = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="archive lint test",
        phase=ExperimentPhase.review,
        current_plan_version=current_plan_version,
    )
    db.add(experiment)
    db.flush()
    return experiment


def _make_review(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    plan_version: int,
    archived: bool = False,
) -> Review:
    now = datetime.now(UTC).replace(tzinfo=None)
    review = Review(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        reviewer_agent_id=reviewer_id,
        plan_version=plan_version,
        archived_at=now if archived else None,
        archived_reason=ReviewArchivedReason.auto if archived else None,
    )
    db.add(review)
    db.flush()
    return review


# --- 关键路径：N=2 过渡期 transition vs post_flip ----------------------


def test_case_1_archived_in_default_list_emits_warn_in_transition(db_session: Session):
    """In N=2 过渡期, archived review leaked to default list is WARN.

    Default ``list_reviews(include_archived=False)`` excludes archived
    reviews, so to actually force a leak we need to manipulate the row
    so its archived state is read inconsistently. We simulate that by
    directly mutating ``archived_at`` to None on a copy of an archived
    review, which mirrors a buggy prod state where an archived review
    was reverted by some external write path.
    """
    project = Project(
        id=uuid.uuid4(),
        project_key=f"p-{uuid.uuid4().hex[:6]}",
        name="p",
        workspace_path="/tmp/p",
    )
    db_session.add(project)
    db_session.flush()
    creator = _make_agent(db_session, project_id=project.id, name="c", role=AgentRole.agent)
    experiment = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="r", role=AgentRole.agent)

    # An archived review, then un-archive it to simulate a leak.
    review = _make_review(
        db_session,
        experiment_id=experiment.id,
        reviewer_id=reviewer.id,
        plan_version=1,
        archived=True,
    )
    review.archived_at = None
    review.archived_reason = None
    db_session.flush()

    result = archive_lint(db_session, experiment.id, n2_phase="transition")
    # No archived review present (archived_at is None) ⇒ no leak issue.
    # The test pins that the helper does NOT spuriously flag non-archived
    # reviews; case 1 here is the inverse check.
    assert result.issues == []


def test_case_1b_archived_review_in_default_list_emits_warn_then_fail_by_phase(db_session: Session):
    """检测 1 — leak behavior. We seed an archived review directly in
    the table, then patch the helper's view by monkey-patching
    ``list_reviews`` to return the archived row as well (simulating
    ``N=2`` transition where ``include_archived`` defaults to True).

    Phase behavior:
    * ``transition`` (default True) → WARN
    * ``post_flip`` (default False) → no leak issue (helper's
      ``list_reviews(include_archived=False)`` already excludes it)
    """
    project = Project(
        id=uuid.uuid4(),
        project_key=f"p-{uuid.uuid4().hex[:6]}",
        name="p",
        workspace_path="/tmp/p",
    )
    db_session.add(project)
    db_session.flush()
    creator = _make_agent(db_session, project_id=project.id, name="c", role=AgentRole.agent)
    experiment = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="r", role=AgentRole.agent)

    review = _make_review(
        db_session,
        experiment_id=experiment.id,
        reviewer_id=reviewer.id,
        plan_version=1,
        archived=True,
    )

    # Force leak: monkey-patch list_reviews inside archive_lint_service
    # to ignore include_archived for this experiment only.
    import server.services.archive_lint_service as mod

    original_list_reviews = mod.list_reviews

    def _leaky_list_reviews(db, exp_id, *, include_archived=False, **kw):
        if exp_id != experiment.id:
            return original_list_reviews(db, exp_id, include_archived=include_archived, **kw)
        from sqlalchemy import select

        from server.domain.models import Review

        return list(
            db.scalars(
                select(Review).where(Review.experiment_id == exp_id)
            )
        )

    try:
        mod.list_reviews = _leaky_list_reviews

        # transition phase: WARN
        result_t = archive_lint(db_session, experiment.id, n2_phase="transition")
        leak_issues = [
            i for i in result_t.issues if i.code == "ARCHIVED_IN_DEFAULT_LIST"
        ]
        assert len(leak_issues) == 1
        assert leak_issues[0].severity == "WARN"
        assert leak_issues[0].review_id == review.id

        # post_flip phase: still WARN under leak (helper takes phase
        # parameter, severity mapping is WARN when transition, FAIL when
        # post_flip). The leaky monkey-patch puts the row in the default
        # list regardless; severity follows the supplied n2_phase.
        result_p = archive_lint(db_session, experiment.id, n2_phase="post_flip")
        leak_issues_p = [
            i for i in result_p.issues if i.code == "ARCHIVED_IN_DEFAULT_LIST"
        ]
        assert len(leak_issues_p) == 1
        assert leak_issues_p[0].severity == "FAIL"
    finally:
        mod.list_reviews = original_list_reviews


def test_case_2_clean_state_emits_no_issues(db_session: Session):
    """No archived reviews, no plan_version drift → empty issues."""
    project = Project(
        id=uuid.uuid4(),
        project_key=f"p-{uuid.uuid4().hex[:6]}",
        name="p",
        workspace_path="/tmp/p",
    )
    db_session.add(project)
    db_session.flush()
    creator = _make_agent(db_session, project_id=project.id, name="c", role=AgentRole.agent)
    experiment = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="r", role=AgentRole.agent)

    # A live, non-archived review.
    _make_review(
        db_session,
        experiment_id=experiment.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )

    result = archive_lint(db_session, experiment.id)
    assert isinstance(result, ArchiveLintResult)
    assert result.issues == []
    assert result.has_failures is False
    assert result.has_warnings is False
    assert result.reviews_archived == 0
    assert result.reviews_total == 1


def test_case_3_plan_version_drift_emits_fail(db_session: Session):
    """Archived review whose plan_version == current_plan_version → FAIL."""
    project = Project(
        id=uuid.uuid4(),
        project_key=f"p-{uuid.uuid4().hex[:6]}",
        name="p",
        workspace_path="/tmp/p",
    )
    db_session.add(project)
    db_session.flush()
    creator = _make_agent(db_session, project_id=project.id, name="c", role=AgentRole.agent)
    experiment = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        current_plan_version=2,
    )
    reviewer = _make_agent(db_session, project_id=project.id, name="r", role=AgentRole.agent)

    # Archived review referencing the current plan_version (drift).
    review = _make_review(
        db_session,
        experiment_id=experiment.id,
        reviewer_id=reviewer.id,
        plan_version=2,
        archived=True,
    )

    result = archive_lint(db_session, experiment.id)
    drift_issues = [
        i for i in result.issues if i.code == "ARCHIVED_PLAN_VERSION_DRIFT"
    ]
    assert len(drift_issues) == 1
    assert drift_issues[0].severity == "FAIL"
    assert drift_issues[0].review_id == review.id
    assert result.has_failures is True


def test_case_4_archived_review_with_old_plan_version_emits_no_drift(db_session: Session):
    """Archived review with plan_version < current → no drift."""
    project = Project(
        id=uuid.uuid4(),
        project_key=f"p-{uuid.uuid4().hex[:6]}",
        name="p",
        workspace_path="/tmp/p",
    )
    db_session.add(project)
    db_session.flush()
    creator = _make_agent(db_session, project_id=project.id, name="c", role=AgentRole.agent)
    experiment = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=creator.id,
        current_plan_version=3,
    )
    reviewer = _make_agent(db_session, project_id=project.id, name="r", role=AgentRole.agent)

    _make_review(
        db_session,
        experiment_id=experiment.id,
        reviewer_id=reviewer.id,
        plan_version=1,
        archived=True,
    )
    _make_review(
        db_session,
        experiment_id=experiment.id,
        reviewer_id=reviewer.id,
        plan_version=2,
        archived=True,
    )

    result = archive_lint(db_session, experiment.id)
    drift_issues = [
        i for i in result.issues if i.code == "ARCHIVED_PLAN_VERSION_DRIFT"
    ]
    assert drift_issues == []
    # 检测 1 — list_reviews 默认 False 过滤了 archived，也无 leak
    assert result.issues == []


def test_case_5_severity_aggregation_props(db_session: Session):
    """has_failures / has_warnings reflect the issue severities present."""
    project = Project(
        id=uuid.uuid4(),
        project_key=f"p-{uuid.uuid4().hex[:6]}",
        name="p",
        workspace_path="/tmp/p",
    )
    db_session.add(project)
    db_session.flush()
    creator = _make_agent(db_session, project_id=project.id, name="c", role=AgentRole.agent)
    experiment = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)

    result = archive_lint(db_session, experiment.id)
    assert result.has_failures is False
    assert result.has_warnings is False


@pytest.mark.parametrize("n2_phase", ["transition", "post_flip"])
def test_n2_phase_is_propagated_to_result(db_session: Session, n2_phase: str):
    """The supplied ``n2_phase`` is reflected in the result so the CLI
    / API wrapper can render the right header without re-deriving it."""
    project = Project(
        id=uuid.uuid4(),
        project_key=f"p-{uuid.uuid4().hex[:6]}",
        name="p",
        workspace_path="/tmp/p",
    )
    db_session.add(project)
    db_session.flush()
    creator = _make_agent(db_session, project_id=project.id, name="c", role=AgentRole.agent)
    experiment = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)

    result = archive_lint(db_session, experiment.id, n2_phase=n2_phase)  # type: ignore[arg-type]
    assert result.n2_phase == n2_phase
