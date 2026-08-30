"""Agent heartbeat PATCH 服务（实验 b3ec2e4d I1 — A1 验收）。

封装 ``agents.last_busy_since`` 写入路径，与既有
``/me/work`` 的 ``last_waker_poll_at`` 刷新（migration 050 / D1）
解耦——busy_since 是 waker state 的扩展信号，刷新时机由 I2 waker
state machine 决定（remind 前 touch / finally 清零），不是 /me/work
polling cycle 附带更新。

设计要点：
- 仅写一列（``last_busy_since``），单列 UPDATE 与既有 D1 模式一致
- 不引入新事务边界；调用方持有 db session
- 不动 last_waker_poll_at / last_api_seen_at（既有路径不动，A8 边界）
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from server.domain.models import Agent


def set_last_busy_since(
    db: Session,
    *,
    agent_id: uuid.UUID,
    busy_since: datetime | None,
) -> None:
    """写 ``agents.last_busy_since``（busy_since=None 视为清零）。

    busy_since 是 timezone-aware UTC datetime；调用方负责
    ``datetime.now(timezone.utc)``。空值 = idle。

    实现：单列 UPDATE（与 ``/me/work`` 的 ``last_waker_poll_at`` 模式
    一致——不在 middleware 副作用，统一在 handler 显式刷新）。
    """
    db.execute(
        update(Agent).where(Agent.id == agent_id).values(last_busy_since=busy_since)
    )
