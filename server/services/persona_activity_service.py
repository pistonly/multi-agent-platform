"""0db51e10 I3(5f): 默认选人逻辑（活跃 agent 时间窗 N=7）服务端 helper.

承接 plan v2 (5c) / acceptance (f):

> 默认选人逻辑单元测试(覆盖 project_id 边界 + agent 状态过滤 +
> N=7 时间窗边界:恰好 7 天 / 8 天动作 / 7 天内无动作三种边界 case)

判定: "活跃 agent" = **最近 7 天(N=7)内有 `log` / `topic` /
`review` 任一动作的同 project agent**。N=7 可配置(默认 7),过期 agent
自动排除。

沿用 ``escalation_resolver._recent_same_role_agent`` 的判定来源
(experiment_logs / comments / reviews 三个 source table),但
本函数返回**所有** role 的活跃 agent 集合(用于 CLI
``--persona-compare`` 默认选人),不做 single-role 过滤与
exclude_agent_id。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, ExperimentLog, Review, TopicComment

# 7 天窗口作为 plan v2 (5c) 默认;CLI/SDK 调用方可通过参数覆盖。
DEFAULT_ACTIVE_WINDOW_DAYS = 7


def list_active_personas(
    db: Session,
    *,
    project_id: uuid.UUID,
    window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Return agent UUIDs active in ``project_id`` within the time window.

    "Active" = any row in ``experiment_logs`` / ``comments`` / ``reviews``
    with ``created_at >= now - window_days`` AND the author /
    reviewer agent belongs to ``project_id``. The function returns the
    **distinct set** of agent UUIDs that satisfy the predicate; no
    ordering guarantee beyond stable iteration order.

    Args:
        db: SQLAlchemy session.
        project_id: Project scope for the lookup.
        window_days: Lookback window in days (default 7, matches plan
            v2 (5c) / (f) acceptance). Must be >= 1; ``window_days=0``
            is rejected because it would always return zero rows and
            provide a confusing default.
        now: Reference "now" timestamp. Defaults to ``datetime.now(timezone.utc)``.
            Tests inject a fixed value to pin the boundary at exactly
            7 / 8 / 0 days.

    Returns:
        List of distinct agent UUIDs active in the window. May be empty
        when no agent has any recent action. Stable order across
        invocations given the same DB state.
    """
    if window_days < 1:
        raise ValueError(
            f"window_days must be >= 1 (got {window_days}); "
            "use the default 7 (plan v2 (5c)) or larger."
        )
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    project_agents = set(
        db.scalars(select(Agent.id).where(Agent.project_id == project_id)).all()
    )
    if not project_agents:
        return []

    active: set[uuid.UUID] = set()

    log_authors = db.execute(
        select(ExperimentLog.author_agent_id).where(
            ExperimentLog.created_at >= cutoff,
            ExperimentLog.author_agent_id.in_(project_agents),
        )
    ).all()
    for (agent_id,) in log_authors:
        active.add(agent_id)

    comment_authors = db.execute(
        select(TopicComment.author_agent_id).where(
            TopicComment.created_at >= cutoff,
            TopicComment.author_agent_id.in_(project_agents),
        )
    ).all()
    for (agent_id,) in comment_authors:
        active.add(agent_id)

    reviewer_ids = db.execute(
        select(Review.reviewer_agent_id).where(
            Review.created_at >= cutoff,
            Review.reviewer_agent_id.in_(project_agents),
        )
    ).all()
    for (agent_id,) in reviewer_ids:
        active.add(agent_id)

    return list(active)
