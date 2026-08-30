"""通知 fan-out 收件人过滤（实验 8b1d20a1 I2）。

核心职责：在 ``notification_service.enqueue_from_event`` 内调用本模块
``filter_recipients``，按「事件类别 + 话题角色白名单」收窄收件人——非白
名单角色对 contextual kind 不收 wakeable，对 obligation kind **仍**收
（A2 + A4 reviewer round2 硬边界）。

判定口径：

- **topic 域 contextual event**（``topic.*`` 但不在 obligation 豁免集）→
  按 ``topic_role_resolver`` 派生的白名单 ``creator ∪ declared ∪
  speakers_current_round ∪ speakers_prev_round`` 过滤；非白名单成员收
  digest（仍记账）但不收 wakeable
- **obligation-wakeable event**（reviewer round2 第 17-22 行列出的硬
  边界：``experiment.phase_changed`` 阶段 ∈ {review, result_review}、
  ``experiment.lifecycle.cancelled``、``experiment.lifecycle.withdrawn``、
  ``review_item.status_changed``）→ 全量 fan-out 给 reviewer（reviewer
  是唯一可清理 obligation 的角色，强行收窄会让 obligation 转 digest 再
  次形成积压回路）
- **其它 event**（``system.*``、``action_item.*``、``experiment.phase_changed``
  非 review/result_review 阶段 等）→ 维持现有行为，不过滤

filter 不改 DB 写路径、不改 envelope 形状、不引入新去重（A8 边界：
``83bf610`` 签名去重语义不动）。仅在 enqueue_from_event 内替换
``_recipients_for_project`` 的输出。

回退策略：白名单解析失败 → 保守放行（保留全量 fan-out，宁可多收不可
漏收 obligation-wakeable）。理由：白名单失败概率极低（FS/projection 双
路径任一可达即可）；失败时多收的 wakeable 可由 I3 digest 自清 + 手动
``notification read`` 收敛，不应阻塞通知主路径。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from server.domain.models import Agent, Project

logger = logging.getLogger(__name__)


# reviewer round2 第 17-22 行 + plan D4+D5 列出的 obligation 豁免 event：
# 即便非白名单角色（reviewer 等旁观者），filter 也必须保留全量 fan-out。
# 注意 ``topic.close_pending`` / ``topic.lifecycle.closed`` / ``topic.round_advanced``
# 不在本集合——它们走白名单过滤（topic.* 按 A2 处理）。
_OBLIGATION_EXEMPT_EVENTS: frozenset[str] = frozenset(
    {
        "experiment.lifecycle.cancelled",
        "experiment.lifecycle.withdrawn",
        "review_item.status_changed",
    }
)


def _is_topic_event(event: str) -> bool:
    return event.startswith("topic.")


def _is_obligation_exempt(event: str, payload: dict | None) -> bool:
    """判断 event 是否属于 obligation 豁免（reviewer 硬边界）。

    ``experiment.phase_changed`` 仅当 phase ∈ {review, result_review} 时
    走 obligation 豁免——其它阶段（draft / approved / running / done）属于
    实验主流程通知，不过滤（plan A2 仅约束 topic.* 走白名单）。
    """
    if event in _OBLIGATION_EXEMPT_EVENTS:
        return True
    if event == "experiment.phase_changed":
        phase = (payload or {}).get("phase") or (payload or {}).get("new_phase")
        return phase in {"review", "result_review"}
    return False


def filter_recipients(
    db: Session,
    *,
    project: Project,
    event: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
    recipients: Iterable[Agent],
) -> list[Agent]:
    """按事件类别 + 话题角色白名单过滤收件人。

    返回过滤后的 Agent 列表；保持原顺序稳定（迭代器输入顺序）。

    入参 ``recipients`` 来自 ``notification_service._recipients_for_project``，
    已排除 actor 与 ``exclude_recipient_ids``。本函数只做"白名单筛选"，
    不做其它排除。

    行为分支：

    - obligation 豁免 event → 全部放行（reviewer 永远在内）
    - topic.* event（除豁免外）→ 解析白名单 → 仅白名单成员收；白名单
      解析失败返回 ``None`` 时**保守放行**（保留全量 fan-out，宁多勿漏）
    - 其它 event → 全部放行（维持现有行为）
    """
    recipients_list = list(recipients)
    if not recipients_list:
        return recipients_list

    if _is_obligation_exempt(event, payload):
        return recipients_list

    if not _is_topic_event(event):
        return recipients_list

    # topic.* event：按白名单过滤
    whitelist = _resolve_whitelist(db, project, target_type, target_id)
    if whitelist is None:
        # 白名单不可达 → 保守放行（宁多勿漏）
        return recipients_list

    filtered = [
        agent
        for agent in recipients_list
        if _agent_name_in_whitelist(agent, whitelist)
    ]
    dropped = len(recipients_list) - len(filtered)
    if dropped:
        logger.info(
            "notification_fanout: event=%s target_id=%s filtered %d/%d recipients "
            "(non-whitelist role)",
            event,
            target_id,
            dropped,
            len(recipients_list),
        )
    return filtered


def _resolve_whitelist(
    db: Session,
    project: Project,
    target_type: str,
    target_id: uuid.UUID | None,
) -> set[str] | None:
    """解析白名单；仅当 target_type=topic 时调用 topic_role_resolver。

    其它 target_type（topic_comment / experiment / review_item / action_item
    等）暂不收窄——按 plan A2 范围仅约束 topic.* event 走白名单；其它目
    标类型 event 的角色过滤留待后续实验。

    target_id 缺失时返回 None（保守放行；调用方应保证 target_id 与
    target_type 一致）。
    """
    if target_type != "topic" or target_id is None:
        return None

    from server.services.topic_role_resolver import resolve_topic_whitelist

    return resolve_topic_whitelist(db, project=project, topic_id=target_id)


def _agent_name_in_whitelist(agent: Agent, whitelist: set[str]) -> bool:
    """判断 Agent 是否在白名单内——按 agent_name 短名匹配。

    Agent 表存的是 ``name`` 字段（短名），与 ``FsTopic.participants`` /
    view.participants 同口径（host / participant / reviewer / admin 等）。
    """
    return (agent.name or "") in whitelist
