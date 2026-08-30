"""话题角色白名单解析（实验 8b1d20a1 I2）。

白名单 = ``creator ∪ declared ∪ speakers_current_round ∪ speakers_prev_round``，
每轮重算（不缓存跨请求）。提供给 ``notification_fanout`` 在 fan-out 时按角色
收窄收件人——非白名单角色对 contextual kind 不收 wakeable，对 obligation kind
**仍**收 wakeable（A2 + A4 硬边界）。

数据源：``fs_source_service.plane_views`` —— 该函数本身已实现「本地 FS 实时
解析优先 + DB 投影快照回退」（fs_source_service.py:473），是话题读路径
统一入口；本模块复用而非另起炉灶。两路径行为在 fs_source_service 内已
保证一致（见 fs_source_service.py:432-433 注释：``FsTopic.participants =
creator ∪ declared ∪ speakers``，与 view.participants 列表等价）。

不可达语义：当 ``plane_views`` 返回空列表（FS 平面不可达且无投影缓存 /
workspace 不可达 / 投影为 None）→ ``resolve_topic_whitelist`` 返回
``None``，filter 层按 "无白名单 = 保守放行" 处理（reviewer round2 硬
边界 obligation-wakeable 必须保留，宁多勿漏）。

每轮重算来自 plan A1：filter 层每次 fan-out 都调一次本函数——避免缓存导致
round1 接力过来的 speaker 在 round2 切换后被遗漏（unread_change 接力不断）。
请求级缓存（同一 request 多次 fan-out 复用一次解析）由调用方决定，本模块
**不**维护跨请求状态。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from server.domain.models import Project

logger = logging.getLogger(__name__)


def resolve_topic_whitelist(
    db: Session,
    *,
    project: Project,
    topic_id: uuid.UUID,
) -> set[str] | None:
    """解析话题的角色白名单（agent_name 短名集合）。

    返回值语义：

    - ``set[str]``：白名单成员 agent_name（"host" / "participant" /
      "reviewer" 等短名；reviewer 等旁观者**不**在内——reviewer 对话题是外来者）
    - ``None``：无法派生白名单（话题不存在 / FS 平面不可达且无投影缓存 /
      元数据解析失败）；filter 层按 "无白名单 = 保守放行" 处理（reviewer
      round2 硬边界 obligation-wakeable 必须保留）

    设计：遍历 ``plane_views`` 全量视图再按 id 过滤——``plane_views`` 本身
    在 fs_source_service 内做 FS/投影选择（带 8 项 LRU 缓存），重复调用
    成本受其内部缓存约束；本模块不再叠缓存。
    """
    view = _load_topic_view(db, project=project, topic_id=topic_id)
    if view is None:
        return None
    return compute_whitelist_from_view(view)


def compute_whitelist_from_view(view) -> set[str]:
    """从 ``_TopicView``（或任何含相同字段的 dataclass）派生白名单。

    算法：

    - creator 永远在内（``view.participants`` 已含 creator）
    - declared_participants（= ack_participants - creator）永远在内（已含）
    - speakers_current_round = ``view.authors_in_round(current_round)``
    - speakers_prev_round = ``view.authors_in_round(current_round - 1)``
    - 三者并集 + 去重

    对 round1 而言 prev_round=0，``authors_in_round(0)`` 返回空集——符合
    plan 口径（round1 接力过来的 speaker 进入 round2 才计入 prev_round）。

    字段来自 ``fs_source_service._TopicView``（server 内部 dataclass）；
    本函数对结构 duck typing，避开循环 import。
    """
    participants = set(view.participants or [])
    if not participants:
        return None  # type: ignore[return-value]
    current_round = view.round_number
    speakers_current = view.authors_in_round(current_round)
    speakers_prev = (
        view.authors_in_round(current_round - 1) if current_round >= 2 else set()
    )
    whitelist = set(participants) | speakers_current | speakers_prev
    if not whitelist:
        return None  # type: ignore[return-value]
    return whitelist


def _load_topic_view(db: Session, *, project: Project, topic_id: uuid.UUID):
    """从 ``plane_views`` 按 topic_id 取单个 view；找不到返回 ``None``。

    异常一律降级——白名单解析失败不应阻塞通知主路径（filter 层保守放行）。
    """
    try:
        from server.services.fs_source_service import plane_views
    except Exception as exc:  # pragma: no cover - 极端 import 失败
        logger.warning("topic_role_resolver: fs_source_service import failed: %s", exc)
        return None

    try:
        for view in plane_views(db, project):
            if view.id == topic_id:
                return view
    except Exception as exc:
        logger.info(
            "topic_role_resolver: plane_views failed for topic %s: %s",
            topic_id,
            exc,
        )
    return None
