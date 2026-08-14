"""0db51e10 I3(5f): 默认选人逻辑 — ``list_active_personas`` 服务端 helper.

Pins plan v2 (5c) + acceptance (f):

> 默认选人逻辑单元测试(覆盖 project_id 边界 + agent 状态过滤 +
> N=7 时间窗边界:恰好 7 天 / 8 天动作 / 7 天内无动作三种边界 case)

判定: "活跃 agent" = 最近 ``window_days`` (默认 7) 天内有
``experiment_logs`` / ``topic_comments`` / ``reviews`` 任一动作的同
project agent。本文件覆盖:

1. ``window_days=7`` 边界:7 天内动作 (in-window)
2. ``window_days=7`` 边界:恰好 8 天前动作 (out-of-window)
3. ``window_days=7`` 边界:7 天内无任何动作 (empty)
4. project_id 隔离:跨 project 动作不漏
5. agent role 隔离:多 role agent 都返回 (helper 不做 single-role 过滤)
6. ``window_days < 1`` 拒绝
7. ``window_days`` 自定义 (e.g. 30 天)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from map_types.enums import AgentRole, ExperimentPhase
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    Experiment,
    ExperimentLog,
    Review,
    Topic,
    TopicComment,
)
from server.services.persona_activity_service import (
    DEFAULT_ACTIVE_WINDOW_DAYS,
    list_active_personas,
)

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    db: Session,
    *,
    project_id: uuid.UUID,
    name: str,
    role: AgentRole = AgentRole.agent,
) -> Agent:
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
) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="persona-activity fixture",
        phase=ExperimentPhase.running,
    )
    db.add(exp)
    db.flush()
    return exp


def _make_topic(
    db: Session,
    *,
    project_id: uuid.UUID,
    creator_id: uuid.UUID,
) -> Topic:
    topic = Topic(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="persona-activity topic",
    )
    db.add(topic)
    db.flush()
    return topic


# ---------------------------------------------------------------------------
# (f) N=7 boundary cases
# ---------------------------------------------------------------------------


def test_list_active_persons_includes_recent_log(db_session: Session, project: dict) -> None:
    """A log within the N=7 window makes the author "active"."""
    project_id = uuid.UUID(project["id"])
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    active = _make_agent(db_session, project_id=project_id, name="active-host")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)

    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            author_agent_id=active.id,
            summary="in-window",
            content_md="",
            log_index=1,
            created_at=now - timedelta(days=3),  # well within 7
        )
    )
    db_session.flush()

    result = list_active_personas(
        db_session, project_id=project_id, now=now
    )
    assert active.id in result


def test_list_active_persons_excludes_log_older_than_window(
    db_session: Session, project: dict
) -> None:
    """A log at exactly ``window_days + 1`` is OUT of the window.

    Boundary check: with ``window_days=7`` and the log at ``now - 8
    days``, the author must NOT be considered active.
    """
    project_id = uuid.UUID(project["id"])
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    stale = _make_agent(db_session, project_id=project_id, name="stale-host")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)

    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            author_agent_id=stale.id,
            summary="8 days old",
            content_md="",
            log_index=1,
            created_at=now - timedelta(days=8),
        )
    )
    db_session.flush()

    result = list_active_personas(
        db_session, project_id=project_id, now=now
    )
    assert stale.id not in result


def test_list_active_persons_includes_activity_at_window_boundary(
    db_session: Session, project: dict
) -> None:
    """A log at ``now - window_days`` (exactly 7 days) IS in the window.

    Boundary check: ``cutoff = now - window_days`` and ``>=`` semantics
    mean a row exactly at the cutoff is included. Confirms we use
    ``>=`` not ``>``.
    """
    project_id = uuid.UUID(project["id"])
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    boundary = _make_agent(db_session, project_id=project_id, name="boundary-host")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)

    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            author_agent_id=boundary.id,
            summary="exactly 7 days",
            content_md="",
            log_index=1,
            created_at=now - timedelta(days=DEFAULT_ACTIVE_WINDOW_DAYS),
        )
    )
    db_session.flush()

    result = list_active_personas(
        db_session, project_id=project_id, now=now
    )
    assert boundary.id in result


def test_list_active_persons_empty_when_no_activity_in_window(
    db_session: Session, project: dict
) -> None:
    """No recent activity → empty result (not admin / not creator)."""
    project_id = uuid.UUID(project["id"])
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    _make_agent(db_session, project_id=project_id, name="dormant-host")

    result = list_active_personas(
        db_session, project_id=project_id, now=now
    )
    assert result == []


# ---------------------------------------------------------------------------
# (f) project_id boundary + role filter
# ---------------------------------------------------------------------------


def test_list_active_persons_excludes_other_projects(
    db_session: Session, project: dict, admin_headers
) -> None:
    """Activity in a different project does NOT leak into the lookup.

    Uses a second project created via the API (admin scope) and adds an
    agent + recent log there. Then queries the original project and
    asserts the cross-project agent is not in the result.
    """
    # Create the second project directly via ORM (the Agent model needs
    # a project row). Avoid pulling in the full ``client`` fixture to
    # keep this test fast and isolated.
    from server.domain.models import Project

    other_project = Project(
        id=uuid.uuid4(),
        project_key="other-persona-activity",
        name="other project",
        workspace_path="/tmp/other",
    )
    db_session.add(other_project)
    db_session.flush()
    other_creator = _make_agent(
        db_session, project_id=other_project.id, name="other-project-creator"
    )
    cross_project_agent = _make_agent(
        db_session, project_id=other_project.id, name="cross-project-agent"
    )
    other_exp = _make_experiment(
        db_session, project_id=other_project.id, creator_id=other_creator.id
    )
    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=other_exp.id,
            author_agent_id=cross_project_agent.id,
            summary="recent but other project",
            content_md="",
            log_index=1,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    db_session.flush()

    project_id = uuid.UUID(project["id"])
    result = list_active_personas(db_session, project_id=project_id)
    assert cross_project_agent.id not in result


def test_list_active_persons_returns_all_roles(db_session: Session, project: dict) -> None:
    """All roles (host / reviewer / admin) appear; no single-role filter.

    The helper is the source of "active personas" for the CLI default,
    so it must surface admins and reviewers as well as agents.
    """
    project_id = uuid.UUID(project["id"])
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    host = _make_agent(
        db_session, project_id=project_id, name="host", role=AgentRole.agent
    )
    reviewer = _make_agent(
        db_session, project_id=project_id, name="reviewer", role=AgentRole.agent
    )
    admin = _make_agent(
        db_session, project_id=project_id, name="admin", role=AgentRole.admin
    )
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    topic = _make_topic(db_session, project_id=project_id, creator_id=creator.id)

    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            author_agent_id=host.id,
            summary="host log",
            content_md="",
            log_index=1,
            created_at=now - timedelta(days=1),
        )
    )
    db_session.add(
        Review(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            reviewer_agent_id=reviewer.id,
            plan_version=1,
            created_at=now - timedelta(days=2),
        )
    )
    db_session.add(
        TopicComment(
            id=uuid.uuid4(),
            topic_id=topic.id,
            author_agent_id=admin.id,
            body="admin note",
            created_at=now - timedelta(days=3),
        )
    )
    db_session.flush()

    result = list_active_personas(
        db_session, project_id=project_id, now=now
    )
    assert host.id in result
    assert reviewer.id in result
    assert admin.id in result


# ---------------------------------------------------------------------------
# (f) window_days validation + custom window
# ---------------------------------------------------------------------------


def test_list_active_persons_rejects_window_days_below_one(
    db_session: Session, project: dict
) -> None:
    """``window_days < 1`` is rejected because it would always return
    zero rows and produce a confusing default."""
    project_id = uuid.UUID(project["id"])
    with pytest.raises(ValueError, match="window_days must be >= 1"):
        list_active_personas(db_session, project_id=project_id, window_days=0)


def test_list_active_persons_respects_custom_window(
    db_session: Session, project: dict
) -> None:
    """``window_days=30`` includes activity from 10 days ago that the
    default N=7 would exclude."""
    project_id = uuid.UUID(project["id"])
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    aged = _make_agent(db_session, project_id=project_id, name="10-day-old-host")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)

    db_session.add(
        ExperimentLog(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            author_agent_id=aged.id,
            summary="10 days ago",
            content_md="",
            log_index=1,
            created_at=now - timedelta(days=10),
        )
    )
    db_session.flush()

    # Default 7 → NOT included
    default_result = list_active_personas(
        db_session, project_id=project_id, now=now
    )
    assert aged.id not in default_result

    # Custom 30 → included
    custom_result = list_active_personas(
        db_session, project_id=project_id, now=now, window_days=30
    )
    assert aged.id in custom_result
