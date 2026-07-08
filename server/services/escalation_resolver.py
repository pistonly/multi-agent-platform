"""STATE_MACHINE.* error escalation contact resolver (experiment 156172e9 I1(b)).

Resolves which agent should be surfaced as the recovery contact when a
STATE_MACHINE.* error fires. The lookup is two-tier:

1. ``Experiment.escalation_target_agent_id`` override (set by host creator
   on a per-experiment basis; NULL by default).
2. Role-based fallback chain documented in plan (b):
   a. Current caller agent (avoid cross-role mis-routing; preferred
      since the caller usually has the most context).
   b. Same-role + same-project active agent (any log / topic-comment /
      review action in the last 7 days).
   c. Admin role (last-resort).

The function never raises — callers use it in error-handling paths
where a missing escalation contact must degrade gracefully (the CLI
falls back to printing the bare error_code + hint instead of crashing).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from map_types.enums import AgentRole
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentLog, Review, TopicComment

# Activity window for "active agent" lookup (plan (b) 2b).
ACTIVE_LOOKBACK_DAYS = 7
_ACTIVITY_SINCE = timedelta(days=ACTIVE_LOOKBACK_DAYS)


def resolve_escalation_target(
    db: Session,
    *,
    experiment: Experiment | None,
    caller_agent_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Pick the best escalation contact for a STATE_MACHINE.* error.

    Returns the agent UUID, or ``None`` if no candidate exists (caller
    should print the hint without an escalation name).

    Args:
        db: SQLAlchemy session.
        experiment: Source experiment if available; used to read the
            ``escalation_target_agent_id`` override.
        caller_agent_id: Agent UUID of the CLI / SDK caller. Preferred
            for the role-based fallback so the caller gets pinged about
            their own action rather than a cross-role mis-route.
        project_id: Project scope for the same-role active-agent lookup.
            Defaults to ``experiment.project_id`` when ``experiment`` is
            provided.
    """
    target_id, _tier = resolve_escalation_target_with_tier(
        db,
        experiment=experiment,
        caller_agent_id=caller_agent_id,
        project_id=project_id,
    )
    return target_id


def resolve_escalation_target_with_tier(
    db: Session,
    *,
    experiment: Experiment | None,
    caller_agent_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, str]:
    """Same as ``resolve_escalation_target`` but also returns the chosen tier.

    Tiers: ``"experiment_override"`` → ``"current_caller"`` →
    ``"same_role_active"`` → ``"admin"`` → ``"none"``. The tier is exposed
    on ``EscalationTargetRead.tier`` so CLI / SDK callers can show the
    user *why* a contact was chosen.
    """
    if experiment is not None and experiment.escalation_target_agent_id is not None:
        return experiment.escalation_target_agent_id, "experiment_override"

    scope_project_id = project_id or (experiment.project_id if experiment is not None else None)
    if scope_project_id is None:
        target = _admin_agent(db)
        return target, "admin" if target is not None else "none"

    # Tier 2a: the current caller (if they belong to this project).
    caller: Agent | None = None
    if caller_agent_id is not None:
        caller = db.get(Agent, caller_agent_id)
        if (
            caller is not None
            and caller.project_id == scope_project_id
            and caller.role != AgentRole.admin
        ):
            return caller.id, "current_caller"

    # Tier 2b: same-role + same-project agent with any recent activity.
    if caller is not None and caller.project_id == scope_project_id:
        target_role = caller.role
        exclude_id = caller.id
    else:
        target_role = AgentRole.agent
        exclude_id = caller.id if caller is not None else None
    same_role_id = _recent_same_role_agent(
        db,
        project_id=scope_project_id,
        role=target_role,
        exclude_agent_id=exclude_id,
    )
    if same_role_id is not None:
        return same_role_id, "same_role_active"

    # Tier 2c: admin — prefer same-project admin, fall back to any admin.
    target = _admin_agent(db, project_id=scope_project_id)
    return target, "admin" if target is not None else "none"


def _recent_same_role_agent(
    db: Session,
    *,
    project_id: uuid.UUID,
    role: AgentRole,
    exclude_agent_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Return the most recently active same-role agent in the project.

    "Active" = any row in ``experiment_logs`` / ``comments`` / ``reviews``
    with ``created_at >= now - 7 days`` AND ``author_agent_id`` belongs
    to an Agent of the requested role and project. Excludes
    ``exclude_agent_id`` so the caller doesn't see themselves.

    Tie-breaker: most recent ``created_at``. Returns ``None`` when no
    same-role agent has any recent activity.

    Implementation note: we run three small SELECTs (one per source
    table) and pick the max ``created_at`` in Python. This avoids the
    awkward ``CompoundSelect`` chaining for three ``union_all``s and
    keeps the query small enough that the optimizer's row counts stay
    predictable. The 7-day lookback bounds the row count regardless.
    """
    cutoff = datetime.now(UTC) - _ACTIVITY_SINCE

    agents_in_project = select(Agent.id).where(
        Agent.project_id == project_id, Agent.role == role
    )
    if exclude_agent_id is not None:
        agents_in_project = agents_in_project.where(Agent.id != exclude_agent_id)
    project_role_agents = set(db.scalars(agents_in_project).all())
    if not project_role_agents:
        return None

    last_seen_by_agent: dict[uuid.UUID, datetime] = {}

    log_rows = db.execute(
        select(ExperimentLog.author_agent_id, ExperimentLog.created_at).where(
            ExperimentLog.created_at >= cutoff,
            ExperimentLog.author_agent_id.in_(project_role_agents),
        )
    ).all()
    for agent_id, ts in log_rows:
        prev = last_seen_by_agent.get(agent_id)
        if prev is None or ts > prev:
            last_seen_by_agent[agent_id] = ts

    comment_rows = db.execute(
        select(TopicComment.author_agent_id, TopicComment.created_at).where(
            TopicComment.created_at >= cutoff,
            TopicComment.author_agent_id.in_(project_role_agents),
        )
    ).all()
    for agent_id, ts in comment_rows:
        prev = last_seen_by_agent.get(agent_id)
        if prev is None or ts > prev:
            last_seen_by_agent[agent_id] = ts

    review_rows = db.execute(
        select(Review.reviewer_agent_id, Review.created_at).where(
            Review.created_at >= cutoff,
            Review.reviewer_agent_id.in_(project_role_agents),
        )
    ).all()
    for agent_id, ts in review_rows:
        prev = last_seen_by_agent.get(agent_id)
        if prev is None or ts > prev:
            last_seen_by_agent[agent_id] = ts

    if not last_seen_by_agent:
        return None
    return max(last_seen_by_agent, key=lambda k: last_seen_by_agent[k])


def _admin_agent(db: Session, *, project_id: uuid.UUID | None = None) -> uuid.UUID | None:
    """Return an admin agent, preferring the same-project admin.

    Order: same-project admin (lex order by id) → any-project admin
    (lex order by id). Returns ``None`` when no admin exists anywhere.
    The order is deterministic per-project — when callers need a
    specific admin, they should set ``experiment.escalation_target_agent_id``
    instead.
    """
    if project_id is not None:
        scoped = (
            select(Agent.id)
            .where(Agent.role == AgentRole.admin, Agent.project_id == project_id)
            .order_by(Agent.id)
            .limit(1)
        )
        row = db.execute(scoped).first()
        if row is not None:
            return row[0]
    global_admin = (
        select(Agent.id)
        .where(Agent.role == AgentRole.admin)
        .order_by(Agent.id)
        .limit(1)
    )
    row = db.execute(global_admin).first()
    return row[0] if row is not None else None


def escalation_label(
    db: Session,
    *,
    escalation_target_id: uuid.UUID | None,
) -> str:
    """Render the escalation contact for CLI / SDK error messages.

    Returns the agent name when found, or a placeholder when the agent
    is missing (deleted / cross-project). Never raises.
    """
    if escalation_target_id is None:
        return "<no escalation contact>"
    agent = db.get(Agent, escalation_target_id)
    if agent is None:
        return f"<agent {escalation_target_id} not found>"
    return f"@{agent.name}"


__all__ = [
    "ACTIVE_LOOKBACK_DAYS",
    "escalation_label",
    "resolve_escalation_target",
    "resolve_escalation_target_with_tier",
]
