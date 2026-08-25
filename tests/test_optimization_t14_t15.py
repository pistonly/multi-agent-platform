"""优化任务 T14 / T15 的回归测试（见 docs/OPTIMIZATION-TASKS.md）。

T14：mention 已读同步批量化——去重 (recipient, source) 对后合成单条
UPDATE；M 个 mention 不再是 M 条 SELECT + K 条 UPDATE。

T15：stalled-lock 扫描消 N+1——最新日志时间一条分组 IN 查询取齐，
project agent 列表每 project 只查一次（holder 排除在 Python 侧）。
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from map_types.enums import ExperimentPhase, MentionSourceType
from sqlalchemy import select

from server.domain.models import (
    AgentRole,
    Experiment,
    ExperimentLog,
    Mention,
    Notification,
    Project,
)
from server.services.auth import create_agent
from server.services.notification_service import (
    mark_agent_mentioned_notifications_read_no_commit,
    notify_stalled_experiment_locks,
)
from tests.test_perf_count_sql_fixture import count_sql_calls

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def _make_project(db_session) -> Project:
    project = Project(
        project_key=f"opt2-{uuid_mod.uuid4().hex[:8]}",
        name="Optimization T14 T15",
        workspace_path="/tmp/opt2",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_notification(
    db_session,
    *,
    recipient_id,
    event: str = "agent.mentioned",
    target_id=None,
    read: bool = False,
) -> Notification:
    row = Notification(
        recipient_agent_id=recipient_id,
        event=event,
        summary=f"notif-{uuid_mod.uuid4().hex[:6]}",
        target_type="topic_comment",
        target_id=target_id,
        read_at=NOW - timedelta(hours=1) if read else None,
    )
    db_session.add(row)
    return row


def _make_mention(db_session, *, mentioned_id, author_id, project_id, source_id) -> Mention:
    mention = Mention(
        mentioned_agent_id=mentioned_id,
        author_agent_id=author_id,
        source_type=MentionSourceType.topic_comment,
        source_id=source_id,
        project_id=project_id,
        excerpt="mention",
    )
    db_session.add(mention)
    return mention


# ---------------------------------------------------------------------------
# T14: mention 已读同步批量单条 UPDATE
# ---------------------------------------------------------------------------


def test_mention_read_sync_marks_only_matching_unread(db_session):
    project = _make_project(db_session)
    mentioned_a, _ = create_agent(db_session, "t14-a", AgentRole.agent, project_id=project.id)
    mentioned_b, _ = create_agent(db_session, "t14-b", AgentRole.agent, project_id=project.id)
    author, _ = create_agent(db_session, "t14-author", AgentRole.agent, project_id=project.id)
    source_1 = uuid_mod.uuid4()
    source_2 = uuid_mod.uuid4()
    source_3 = uuid_mod.uuid4()

    m1 = _make_mention(db_session, mentioned_id=mentioned_a.id, author_id=author.id,
                       project_id=project.id, source_id=source_1)
    m2 = _make_mention(db_session, mentioned_id=mentioned_b.id, author_id=author.id,
                       project_id=project.id, source_id=source_2)
    # 同一 (recipient, source) 的重复 mention：只应产生一个匹配条件。
    m3 = _make_mention(db_session, mentioned_id=mentioned_a.id, author_id=author.id,
                       project_id=project.id, source_id=source_1)
    _make_notification(db_session, recipient_id=mentioned_a.id, target_id=source_1)  # 命中
    _make_notification(db_session, recipient_id=mentioned_b.id, target_id=source_2)  # 命中
    _make_notification(db_session, recipient_id=mentioned_a.id, target_id=source_3)  # 无对应 mention
    _make_notification(db_session, recipient_id=mentioned_a.id, target_id=source_1, read=True)  # 已读
    _make_notification(db_session, recipient_id=mentioned_a.id, event="experiment.phase_changed",
                       target_id=source_1)  # 事件不同
    db_session.flush()

    touched = mark_agent_mentioned_notifications_read_no_commit(
        db_session, mentions=[m1, m2, m3], now=NOW
    )

    assert touched == 2
    unread_left = (
        db_session.query(Notification)
        .filter(Notification.read_at.is_(None))
        .count()
    )
    # 只剩「无 mention」与「事件不同」两条未读。
    assert unread_left == 2


def test_mention_read_sync_sql_is_single_update(db_session, engine):
    """T14 回归守卫：任意数量的 mention 只允许恰好 1 条 UPDATE、0 条 SELECT。"""
    project = _make_project(db_session)
    mentioned, _ = create_agent(db_session, "t14-bulk", AgentRole.agent, project_id=project.id)
    author, _ = create_agent(db_session, "t14-bulk-author", AgentRole.agent, project_id=project.id)
    mentions = []
    for _ in range(4):
        source = uuid_mod.uuid4()
        mentions.append(
            _make_mention(db_session, mentioned_id=mentioned.id, author_id=author.id,
                          project_id=project.id, source_id=source)
        )
        _make_notification(db_session, recipient_id=mentioned.id, target_id=source)
    db_session.flush()

    with count_sql_calls(engine, label="t14.mention-read-sync") as counter:
        touched = mark_agent_mentioned_notifications_read_no_commit(
            db_session, mentions=mentions, now=NOW
        )

    assert touched == 4
    updates = [s for s, _ in counter.statements if s.lstrip().upper().startswith("UPDATE")]
    selects = [s for s, _ in counter.statements if s.lstrip().upper().startswith("SELECT")]
    assert len(updates) == 1, counter.summary()
    assert selects == [], counter.summary()


def test_mention_read_sync_empty_and_non_mention_inputs(db_session):
    project = _make_project(db_session)
    create_agent(db_session, "t14-empty", AgentRole.agent, project_id=project.id)
    assert mark_agent_mentioned_notifications_read_no_commit(db_session, mentions=[]) == 0
    # 非 Mention 对象（调用方防御分支）不产生任何 SQL 行为。
    assert mark_agent_mentioned_notifications_read_no_commit(db_session, mentions=[object()]) == 0


# ---------------------------------------------------------------------------
# T15: stalled-lock 扫描消 N+1
# ---------------------------------------------------------------------------


def _locked_experiment(db_session, project, host, *, acquired_at, ttl_seconds=1000) -> Experiment:
    exp = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        title=f"stalled-{uuid_mod.uuid4().hex[:6]}",
        phase=ExperimentPhase.running,
        current_plan_version=1,
        lock_holder_experiment_id=None,
        lock_acquired_at=acquired_at,
        lock_ttl_seconds=ttl_seconds,
    )
    db_session.add(exp)
    db_session.flush()
    exp.lock_holder_experiment_id = exp.id
    db_session.flush()
    return exp


def test_stalled_scan_batches_log_and_agent_queries(db_session, engine):
    """T15 回归守卫：N 个候选实验的日志时间查询恒为 1 条、agent 查询每 project 1 条。"""
    project = _make_project(db_session)
    host, _ = create_agent(db_session, "t15-host", AgentRole.agent, project_id=project.id)
    reviewer, _ = create_agent(db_session, "t15-reviewer", AgentRole.agent, project_id=project.id)

    # 候选 1：ratio 0.9 ≥ wake_threshold（host wakeable + reviewer digest）。
    _locked_experiment(db_session, project, host, acquired_at=NOW - timedelta(seconds=900))
    # 候选 2/3：ratio 介于 progress/wake 阈值之间（仅 digest）。
    _locked_experiment(db_session, project, host, acquired_at=NOW - timedelta(seconds=600))
    _locked_experiment(db_session, project, host, acquired_at=NOW - timedelta(seconds=550))
    # 锁后有进展日志的候选：被跳过，但仍参与同一条分组日志查询。
    progress_exp = _locked_experiment(db_session, project, host, acquired_at=NOW - timedelta(seconds=900))
    db_session.add(
        ExperimentLog(
            experiment_id=progress_exp.id,
            author_agent_id=host.id,
            summary="progress",
            content_md="made progress",
            log_index=1,
            created_at=NOW - timedelta(seconds=100),
        )
    )
    # 低于 progress_threshold：Pass 1 即被 Python 过滤，不进日志批量查询。
    _locked_experiment(db_session, project, host, acquired_at=NOW - timedelta(seconds=100))
    db_session.flush()

    with count_sql_calls(engine, label="t15.stalled-scan") as counter:
        emitted = notify_stalled_experiment_locks(db_session, now=NOW)

    # wake_exp: host wakeable + reviewer digest；两个 digest_exp：reviewer digest。
    assert len(emitted) == 4
    log_queries = [s for s, _ in counter.statements if "experiment_logs" in s]
    agent_queries = [s for s, _ in counter.statements if "FROM agents" in s]
    assert len(log_queries) == 1, counter.summary()
    assert len(agent_queries) == 1, counter.summary()

    host_notifs = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == host.id)
    ).all()
    reviewer_notifs = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == reviewer.id)
    ).all()
    assert len(host_notifs) == 1
    assert len(reviewer_notifs) == 3


def test_stalled_scan_agent_query_once_per_project(db_session, engine):
    project_a = _make_project(db_session)
    project_b = _make_project(db_session)
    host_a, _ = create_agent(db_session, "t15-host-a", AgentRole.agent, project_id=project_a.id)
    host_b, _ = create_agent(db_session, "t15-host-b", AgentRole.agent, project_id=project_b.id)
    create_agent(db_session, "t15-member-a", AgentRole.agent, project_id=project_a.id)
    _locked_experiment(db_session, project_a, host_a, acquired_at=NOW - timedelta(seconds=900))
    _locked_experiment(db_session, project_b, host_b, acquired_at=NOW - timedelta(seconds=900))
    db_session.flush()

    with count_sql_calls(engine, label="t15.two-projects") as counter:
        emitted = notify_stalled_experiment_locks(db_session, now=NOW)

    # project_a：host_a wakeable + member_a digest；project_b 只有 host_b
    # （digest 收件人 = 成员 - holder = 空）。
    assert len(emitted) == 3
    agent_queries = [s for s, _ in counter.statements if "FROM agents" in s]
    log_queries = [s for s, _ in counter.statements if "experiment_logs" in s]
    assert len(agent_queries) == 2, counter.summary()  # 每 project 恰 1 条
    assert len(log_queries) == 1, counter.summary()  # 跨 project 仍 1 条分组查询
