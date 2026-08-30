"""通知 digest 自清服务（实验 8b1d20a1 I3）。

职责：周期任务（建议 24h）扫描 unread notification，对 **digest 类且
``first_event_at`` 距今 ≥7 天** 的条目自动 read 并写审计日志。**严禁**
清 obligation-wakeable —— 即便 ≥7 天未读也不动（plan D5：豁免
``experiment.phase_changed(review/result_review)`` /
``experiment.lifecycle.cancelled/withdrawn``）。

与 ``notification_fanout`` 的语义分工：

- ``notification_fanout`` 在 *写入* 端按角色白名单过滤收件人（A2 + A4）
- 本模块在 *读取/收敛* 端清理过期 digest —— 让 reviewer waker 不再因
  积压 digest 反复空唤醒（A5 + A6）

双层安全网：D5 提到 "即便 ≥7 天未读 obligation-wakeable 也不自清"。
本模块以 ``category == NotificationCategory.digest`` 作为**首要**判定
—— ``classify(event)`` 已保证 obligation-wakeable event 落 wakeable
不落 digest。但为防御性编程，仍在 audit 日志中记录 event 名 + category
供事后复盘。

调用入口：

- 周期调度：留给运维侧（cron / `map cleanup run` CLI / FastAPI
  background task）；本模块**不**自带循环，避免与 server 进程耦合
- 手动触发：``cleanup_digest_notifications(db)`` 直接调
- 测试触发：传 ``now`` 参数覆盖时钟，便于断言边界（≥7 天 vs <7 天）
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from map_types.enums import NotificationCategory
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Notification
from server.services.audit_service import log_no_commit

logger = logging.getLogger(__name__)


# 与 reviewer round2 硬边界同口径：这些 event 即便 ≥7 天未读也不自清。
# 实际上 ``category=digest`` 已天然筛掉（它们走 wakeable 不走 digest），
# 但本模块作为安全网显式列出，便于审计 / 测试 / 防御性编程。
_OBLIGATION_EXEMPT_EVENTS: frozenset[str] = frozenset(
    {
        "experiment.lifecycle.cancelled",
        "experiment.lifecycle.withdrawn",
        "review_item.status_changed",
    }
)


# 默认阈值（plan A5：≥7 天 digest 自动 read）。可通过参数覆盖供测试使用。
DEFAULT_CUTOFF_DAYS = 7


def cleanup_digest_notifications(
    db: Session,
    *,
    cutoff_days: int = DEFAULT_CUTOFF_DAYS,
    now: datetime | None = None,
    actor_agent_id: uuid.UUID | None = None,
) -> int:
    """清理过期 digest 类通知；返回清理条数。

    参数：

    - ``cutoff_days``: 阈值天数；``first_event_at <= now - cutoff_days`` 才清理
    - ``now``: 覆盖时钟（测试用）；默认 ``datetime.now(timezone.utc)``
    - ``actor_agent_id``: 审计日志的 actor agent id；为 None 时落空
      （audit_logs.agent_id 可空），审计日志仍写但关联不到具体 agent

    行为：

    1. 查 ``unread + category=digest + first_event_at <= now - cutoff_days``
       的所有 Notification（不限 project —— digest 是 user-level 视角的
       通知，不按 project 隔离）
    2. 对每条：设 ``read_at = now``，写 audit_log（action=
       ``auto_drain_digest_7d`` / target_type=``notification`` /
       target_id=notification.id / summary 含 event 名 + 距今天数）
    3. 单事务提交；返回清理条数

    异常：obligation 豁免 event（即便 category 被误标为 digest）→ 跳过
    并记录 warning 日志，便于后续追溯 schema 漂移。

    注意：本函数使用 ``Notification.first_event_at``（不是
    ``created_at``）—— 前者是事件首次发生时间，后者是 notification row
    插入时间；7 天口径应锚定事件发生时间（reviewer round2 第 30-31 行）。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cutoff_days)

    stmt = (
        select(Notification)
        .where(
            Notification.read_at.is_(None),
            Notification.category == NotificationCategory.digest,
            Notification.first_event_at.isnot(None),
            Notification.first_event_at <= cutoff,
        )
        .order_by(Notification.first_event_at.asc())
    )
    candidates = list(db.scalars(stmt))
    if not candidates:
        return 0

    drained = 0
    skipped_obligation = 0
    for notif in candidates:
        if notif.event in _OBLIGATION_EXEMPT_EVENTS:
            # 防御性：obligation-wakeable 不应被分类为 digest（classify 已保证）；
            # 若出现则跳过 + 警告，不自动 read。
            logger.warning(
                "notification_cleanup: skip obligation-exempt event %s on notif %s "
                "(category=%s; expected wakeable)",
                notif.event,
                notif.id,
                notif.category,
            )
            skipped_obligation += 1
            continue

        notif.read_at = now
        days_old = (
            (now - notif.first_event_at).days if notif.first_event_at else None
        )
        log_no_commit(
            db,
            action="auto_drain_digest_7d",
            target_type="notification",
            target_id=notif.id,
            agent_id=actor_agent_id,
            project_id=notif.project_id,
            summary=(
                f"auto-read digest event={notif.event!r} "
                f"recipient={notif.recipient_agent_id} days_old={days_old}"
            ),
            payload={
                "event": notif.event,
                "category": notif.category.value if hasattr(notif.category, "value") else str(notif.category),
                "recipient_agent_id": str(notif.recipient_agent_id)
                if notif.recipient_agent_id
                else None,
                "first_event_at": notif.first_event_at.isoformat()
                if notif.first_event_at
                else None,
                "days_old": days_old,
                "actor": "system",
                "reason": "auto_drain_digest_7d",
            },
        )
        drained += 1

    if drained:
        db.commit()
        logger.info(
            "notification_cleanup: drained %d digest notifications (skipped %d obligation)",
            drained,
            skipped_obligation,
        )
    return drained


def find_system_agent(db: Session) -> Agent | None:
    """查找 system agent（审计日志 actor）—— 留作运维侧可选调用。

    本模块不强依赖该函数；调用方可传 ``actor_agent_id=None``。该函数
    仅为调用方提供便利：``get_or_create_system_agent`` 之类的 helper
    若不存在则返回 None，audit_logs.agent_id 落空亦合法。
    """
    stmt = select(Agent).where(Agent.role == "system").limit(1)
    return db.scalars(stmt).first()
