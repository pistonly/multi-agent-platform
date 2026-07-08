"""STATE_MACHINE.* escalation resolver + experiments.escalation_target_agent_id
(experiment 156172e9 I1(b)).

Pins plan (b) acceptance:

- ``experiments`` table has a nullable ``escalation_target_agent_id``
  column (FK to ``agents.id``) after migration 033 runs.
- ``resolve_escalation_target`` returns the experiment override when
  set (Tier 1).
- Falls back to current caller when no override (Tier 2a).
- Falls back to most-recent same-role same-project active agent when
  caller is missing (Tier 2b; 7-day lookback across logs / comments /
  reviews).
- Falls back to admin when no same-role candidate (Tier 2c).
- Never raises — returns ``None`` if even admin is missing.
- Cross-project callers do NOT leak into the same-project lookup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from map_types.enums import AgentRole, ExperimentPhase
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    Experiment,
    ExperimentLog,
    Topic,
    TopicComment,
)
from server.services.escalation_resolver import (
    ACTIVE_LOOKBACK_DAYS,
    escalation_label,
    resolve_escalation_target,
)


def _make_agent(
    db: Session,
    *,
    project_id: uuid.UUID,
    name: str,
    role: AgentRole,
) -> Agent:
    """Create an agent directly in the DB (avoids the auth bootstrap)."""
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
    escalation_target_id: uuid.UUID | None = None,
) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="escalation-target experiment",
        phase=ExperimentPhase.running,
    )
    if escalation_target_id is not None:
        exp.escalation_target_agent_id = escalation_target_id
    db.add(exp)
    db.flush()
    return exp


def test_experiment_model_has_escalation_target_column() -> None:
    """The Experiment ORM model exposes ``escalation_target_agent_id``."""
    cols = {c.name for c in Experiment.__table__.columns}
    assert "escalation_target_agent_id" in cols


def test_migration_adds_escalation_target_column(db_session: Session) -> None:
    """Migration 033 added the column to ``experiments`` (idempotent).

    The default ``engine`` fixture already ran ``alembic upgrade head`` so
    the column is present. We assert via SQLAlchemy reflection.
    """
    from sqlalchemy import inspect

    inspector = inspect(db_session.bind)
    cols = {c["name"] for c in inspector.get_columns("experiments")}
    assert "escalation_target_agent_id" in cols


def test_resolve_escalation_target_tier1_experiment_override(
    db_session: Session, project: dict
) -> None:
    """When ``escalation_target_agent_id`` is set, it always wins."""
    project_id = uuid.UUID(project["id"])
    caller = _make_agent(db_session, project_id=project_id, name="caller", role=AgentRole.agent)
    override = _make_agent(
        db_session, project_id=project_id, name="override-recipient", role=AgentRole.agent
    )
    exp = _make_experiment(
        db_session,
        project_id=project_id,
        creator_id=caller.id,
        escalation_target_id=override.id,
    )
    target = resolve_escalation_target(
        db_session, experiment=exp, caller_agent_id=caller.id
    )
    assert target == override.id


def test_resolve_escalation_target_tier2a_current_caller(
    db_session: Session, project: dict
) -> None:
    """No override → current caller is the escalation target."""
    project_id = uuid.UUID(project["id"])
    caller = _make_agent(db_session, project_id=project_id, name="caller", role=AgentRole.agent)
    exp = _make_experiment(
        db_session, project_id=project_id, creator_id=caller.id
    )
    target = resolve_escalation_target(
        db_session, experiment=exp, caller_agent_id=caller.id
    )
    assert target == caller.id


def test_resolve_escalation_target_tier2b_same_role_recent(
    db_session: Session, project: dict
) -> None:
    """No override + no caller → most-recent same-role active agent.

    Setup: caller unknown, two agent-role agents in the project. Agent A
    has a recent log (in-window), Agent B has stale activity (out of
    window). Tier 2b should pick A.
    """
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator", role=AgentRole.agent)
    agent_a = _make_agent(db_session, project_id=project_id, name="agent-a", role=AgentRole.agent)
    agent_b = _make_agent(db_session, project_id=project_id, name="agent-b", role=AgentRole.agent)
    exp = _make_experiment(
        db_session, project_id=project_id, creator_id=creator.id
    )
    # Add a recent log for agent_a (in-window).
    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            author_agent_id=agent_a.id,
            summary="recent activity",
            content_md="",
            log_index=1,
        )
    )
    # Add a stale log for agent_b (outside the 7-day window).
    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            author_agent_id=agent_b.id,
            summary="stale activity",
            content_md="",
            log_index=2,
            created_at=datetime.now(UTC) - timedelta(days=ACTIVE_LOOKBACK_DAYS + 30),
        )
    )
    db_session.flush()

    target = resolve_escalation_target(
        db_session, experiment=exp, caller_agent_id=None
    )
    assert target == agent_a.id


def test_resolve_escalation_target_tier2b_recent_activity_via_comment(
    db_session: Session, project: dict
) -> None:
    """Activity via Comment counts as recent (covers the comments path)."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator", role=AgentRole.agent)
    agent_c = _make_agent(db_session, project_id=project_id, name="agent-c", role=AgentRole.agent)
    exp = _make_experiment(
        db_session, project_id=project_id, creator_id=creator.id
    )
    # Need a topic to attach the comment to (FK constraint).
    topic = Topic(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator.id,
        title="escalation test topic",
    )
    db_session.add(topic)
    db_session.flush()
    db_session.add(
        TopicComment(
            id=uuid.uuid4(),
            topic_id=topic.id,
            author_agent_id=agent_c.id,
            body="recent comment by agent-c",
        )
    )
    db_session.flush()

    target = resolve_escalation_target(
        db_session, experiment=exp, caller_agent_id=None
    )
    assert target == agent_c.id


def test_resolve_escalation_target_tier2c_admin_fallback(
    db_session: Session, project: dict
) -> None:
    """No override + no caller + no same-role activity → admin."""
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator", role=AgentRole.agent)
    admin = _make_agent(db_session, project_id=project_id, name="ops-admin", role=AgentRole.admin)
    exp = _make_experiment(
        db_session, project_id=project_id, creator_id=creator.id
    )

    target = resolve_escalation_target(
        db_session, experiment=exp, caller_agent_id=None
    )
    assert target == admin.id


def test_resolve_escalation_target_tier2c_falls_back_when_no_role_activity(
    db_session: Session, project: dict
) -> None:
    """No override + no caller + no recent role activity → admin (any project).

    The default ``project`` fixture creates an admin (``admin-agent``),
    so tier 2c must return that admin regardless of which project they
    live in (since the caller has no role context to anchor on).
    """
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="solo", role=AgentRole.agent)
    # No logs / comments / reviews → tier 2b has no candidate.
    exp = _make_experiment(
        db_session, project_id=project_id, creator_id=creator.id
    )
    target = resolve_escalation_target(
        db_session, experiment=exp, caller_agent_id=None
    )
    # Tier 2c returns any admin (the fixture's admin-agent is in this
    # project so the same-project lookup wins).
    assert target is not None
    assert target != creator.id
    agent = db_session.get(Agent, target)
    assert agent is not None
    assert agent.role == AgentRole.admin


def test_resolve_escalation_target_cross_project_caller_excluded(
    db_session: Session, project: dict, admin_headers: dict
) -> None:
    """A caller from a *different* project does not leak in.

    Setup: project A has its own agent + experiment. Create a separate
    project B with its own caller agent. Tier 2a must reject the cross-
    project caller (caller.project_id != experiment.project_id) and fall
    through to admin.
    """
    # Project A: from the existing fixture.
    project_a_id = uuid.UUID(project["id"])
    caller_a = _make_agent(db_session, project_id=project_a_id, name="caller-a", role=AgentRole.agent)
    admin_a = _make_agent(db_session, project_id=project_a_id, name="admin-a", role=AgentRole.admin)
    exp_a = _make_experiment(
        db_session, project_id=project_a_id, creator_id=caller_a.id
    )

    # Project B: separate project + caller.
    from map_types.schemas import ProjectCreate

    from server.services import project_service

    project_b = project_service.create_project(
        db_session,
        ProjectCreate(
            project_key=f"escalation-other-{uuid.uuid4().hex[:8]}",
            name="escalation-other-project",
            workspace_path="/tmp/escalation-other",
        ),
        author_agent_id=caller_a.id,
    )
    caller_b = _make_agent(
        db_session, project_id=project_b.id, name="caller-b", role=AgentRole.agent
    )
    db_session.flush()

    target = resolve_escalation_target(
        db_session, experiment=exp_a, caller_agent_id=caller_b.id
    )
    # Tier 2a rejects cross-project caller → falls to admin of project A.
    assert target == admin_a.id
    assert target != caller_b.id


def test_resolve_escalation_target_handles_missing_experiment(db_session: Session) -> None:
    """``experiment=None`` falls through to admin lookup without crashing.

    Without an experiment, no project scope exists → tier 2c admin lookup
    runs against an empty agents table → returns ``None`` cleanly.
    """
    target = resolve_escalation_target(
        db_session, experiment=None, caller_agent_id=None
    )
    assert target is None


def test_escalation_label_renders_agent_name(db_session: Session, project: dict) -> None:
    """``escalation_label`` returns ``@<name>`` for a known agent."""
    project_id = uuid.UUID(project["id"])
    agent = _make_agent(db_session, project_id=project_id, name="on-call", role=AgentRole.agent)
    label = escalation_label(db_session, escalation_target_id=agent.id)
    assert label == "@on-call"


def test_escalation_label_handles_missing_agent(db_session: Session) -> None:
    """``escalation_label`` returns a placeholder for unknown agent UUID."""
    fake = uuid.uuid4()
    label = escalation_label(db_session, escalation_target_id=fake)
    assert "not found" in label


def test_escalation_label_handles_none(db_session: Session) -> None:
    """``escalation_label(None)`` returns the no-contact placeholder."""
    label = escalation_label(db_session, escalation_target_id=None)
    assert label == "<no escalation contact>"
