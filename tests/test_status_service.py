"""build_waker_heartbeats busy/stale 分支回归测试（实验 b3ec2e4d I5）。

被测目标：``server.services.status_service.build_waker_heartbeats`` 在
``last_busy_since`` 引入后的分支计算：

- ``busy_since`` 非空 + < busy_tolerance → busy 不算 stale
- ``busy_since`` 非空 + > busy_tolerance → stale（覆盖「卡死但 polling
  仍新」盲区）
- ``busy_since`` 空 + ``last_waker_poll_at`` 旧 → 沿用 D1 既有 stale
- ``busy_since`` 空 + ``last_waker_poll_at`` 新 → ok
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from server.domain.models import Agent, AgentRole
from server.services.status_service import build_waker_heartbeats


@pytest.fixture()
def probe_agent(db_session) -> Agent:
    """Create a fresh agent in the test DB; nested savepoint rolls back on
    teardown so we don't pollute the schema for other tests."""
    agent = Agent(
        id=uuid.uuid4(),
        name=f"probe-{uuid.uuid4().hex[:8]}",
        role=AgentRole.agent,
        api_token_hash="dummy-hash",
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def test_idle_old_poll_is_stale(db_session, probe_agent):
    """D1 既有语义：busy_since 空 + poll 旧 → stale=True。"""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    probe_agent.last_waker_poll_at = now - timedelta(hours=2)
    probe_agent.last_busy_since = None
    db_session.flush()

    rows = build_waker_heartbeats(db_session, threshold_minutes=15, now=now)
    r = next(r for r in rows if r.agent_id == probe_agent.id)
    assert r.stale is True
    assert r.last_busy_since is None
    assert r.last_waker_poll_at == probe_agent.last_waker_poll_at


def test_idle_fresh_poll_is_not_stale(db_session, probe_agent):
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    probe_agent.last_waker_poll_at = now - timedelta(minutes=1)
    probe_agent.last_busy_since = None
    db_session.flush()

    rows = build_waker_heartbeats(db_session, threshold_minutes=15, now=now)
    r = next(r for r in rows if r.agent_id == probe_agent.id)
    assert r.stale is False


def test_busy_recent_not_stale(db_session, probe_agent):
    """A3：busy_since 新 + poll 旧 → 不算 stale（busy 容忍）。"""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    probe_agent.last_waker_poll_at = now - timedelta(hours=2)
    probe_agent.last_busy_since = now - timedelta(minutes=5)
    db_session.flush()

    rows = build_waker_heartbeats(db_session, threshold_minutes=15, now=now)
    r = next(r for r in rows if r.agent_id == probe_agent.id)
    assert r.stale is False, f"busy 5min 不应 stale: {r}"
    assert r.last_busy_since == probe_agent.last_busy_since


def test_busy_exceeds_tolerance_is_stale(db_session, probe_agent):
    """A3 边界：busy_since 超过 busy_tolerance → stale（覆盖卡死盲区）。"""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    probe_agent.last_waker_poll_at = now - timedelta(minutes=1)
    # busy_tolerance = max(expected_remind_runtime=30, 2*15) = 30min
    # 设 2h → 超 tolerance → stale=True
    probe_agent.last_busy_since = now - timedelta(hours=2)
    db_session.flush()

    rows = build_waker_heartbeats(db_session, threshold_minutes=15, now=now)
    r = next(r for r in rows if r.agent_id == probe_agent.id)
    assert r.stale is True, f"busy 2h 应 stale: {r}"
