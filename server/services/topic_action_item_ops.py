"""Topic action-item list / mutate / serialize helpers.

拆分自 ``topic_service.py`` 以缓解超大模块可读性问题。
本模块负责 action item 的读序列化与生命周期 mutation：
- ``_action_item_read`` / suggest-link / linked-phase batch helpers
- ``list_action_items``
- ``deliver`` / ``complete`` / ``cancel`` / ``link``
- ``mark_wake_sent_action_item`` / ``mark_stale_action_item``

``topic_service`` re-exports 公开与私有 API，保持现有 import 不破。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from map_types.enums import ActionItemCategory, ExperimentPhase, TopicActionItemStatus
from map_types.schemas import ActionItemCancel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import Agent, Experiment, Topic, TopicActionItem
from server.domain.schemas import TopicActionItemRead
from server.services import action_item_service, audit_service, notification_service
from server.services._lookups import get_project
from server.services.errors import ConflictError, ForbiddenError, NotFoundError
from server.services.permissions import is_admin


def _suggest_linked_experiment(db: Session, item: TopicActionItem) -> tuple[uuid.UUID | None, str | None]:
    """Find a recently-done experiment in the same project whose owner matches
    the action item's owner and whose title is a substring match (case-insensitive,
    space-insensitive) of the action item title. Returns ``(None, None)`` when
    the item is not eligible (closed, already linked, no owner) or no match.
    """
    if item.status != TopicActionItemStatus.open:
        return None, None
    if item.linked_experiment_id is not None:
        return None, None
    if item.owner_agent_id is None:
        return None, None

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    item_norm = item.title.lower().replace(" ", "")
    candidates = db.scalars(
        select(Experiment)
        .where(
            Experiment.project_id == item.project_id,
            Experiment.creator_agent_id == item.owner_agent_id,
            Experiment.phase == ExperimentPhase.done,
            Experiment.deleted_at.is_(None),
            Experiment.archived_at.is_(None),
            Experiment.updated_at >= cutoff,
        )
        .order_by(Experiment.updated_at.desc())
        .limit(50)
    ).all()
    for exp in candidates:
        exp_norm = exp.title.lower().replace(" ", "")
        if not exp_norm or not item_norm:
            continue
        if exp_norm in item_norm or item_norm in exp_norm:
            return exp.id, exp.title
    return None, None


def _suggest_linked_experiments_batch(
    db: Session, items: list[TopicActionItem]
) -> dict[uuid.UUID, tuple[uuid.UUID | None, str | None]]:
    """Batch counterpart of :func:`_suggest_linked_experiment`.

    List / decision-read 路径会对多个 action_item 逐条调用 read，若每条都
    单独查候选实验会产生 N+1（每条一次 SQL + 50 行内存匹配）。本函数按
    ``(project_id, owner_agent_id)`` 分桶，每桶只查一次候选实验，再在 Python
    内做子串匹配。不合格（非 open / 已 link / 无 owner）的 item 直接映射到
    ``(None, None)``。
    """
    result: dict[uuid.UUID, tuple[uuid.UUID | None, str | None]] = {}
    eligible: list[TopicActionItem] = []
    for item in items:
        if (
            item.status == TopicActionItemStatus.open
            and item.linked_experiment_id is None
            and item.owner_agent_id is not None
        ):
            eligible.append(item)
        else:
            result[item.id] = (None, None)
    if not eligible:
        return result

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    groups: dict[tuple[uuid.UUID, uuid.UUID | None], list[TopicActionItem]] = {}
    for item in eligible:
        groups.setdefault((item.project_id, item.owner_agent_id), []).append(item)

    for (project_id, owner_id), group_items in groups.items():
        candidates = db.scalars(
            select(Experiment)
            .where(
                Experiment.project_id == project_id,
                Experiment.creator_agent_id == owner_id,
                Experiment.phase == ExperimentPhase.done,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
                Experiment.updated_at >= cutoff,
            )
            .order_by(Experiment.updated_at.desc())
            .limit(50)
        ).all()
        for item in group_items:
            item_norm = item.title.lower().replace(" ", "")
            match: tuple[uuid.UUID | None, str | None] = (None, None)
            if item_norm:
                for exp in candidates:
                    exp_norm = exp.title.lower().replace(" ", "")
                    if exp_norm and (exp_norm in item_norm or item_norm in exp_norm):
                        match = (exp.id, exp.title)
                        break
            result[item.id] = match
    return result


def _linked_experiment_phases_batch(
    db: Session, items: list[TopicActionItem]
) -> dict[uuid.UUID, ExperimentPhase | None]:
    """Batch-fetch ``linked_experiment_phase`` for a list of action items.

    Returns a mapping ``{item.id: phase | None}``. Items without a linked
    experiment map to ``None`` without hitting the DB. Used by list / todos
    read paths to avoid an N+1 when serializing many action items.
    """
    result: dict[uuid.UUID, ExperimentPhase | None] = {}
    linked_ids: list[uuid.UUID] = []
    for item in items:
        if item.linked_experiment_id is not None:
            linked_ids.append(item.linked_experiment_id)
        else:
            result[item.id] = None
    if not linked_ids:
        return result
    rows = db.execute(
        select(Experiment.id, Experiment.phase).where(Experiment.id.in_(linked_ids))
    ).all()
    phase_by_id: dict[uuid.UUID, ExperimentPhase] = {row[0]: row[1] for row in rows}
    for item in items:
        if item.linked_experiment_id is not None:
            result[item.id] = phase_by_id.get(item.linked_experiment_id)
    return result


def _action_item_read(
    db: Session,
    item: TopicActionItem,
    *,
    suggested: tuple[uuid.UUID | None, str | None] | None = None,
    linked_experiment_phase: ExperimentPhase | None = None,
    linked_experiment_phase_provided: bool = False,
) -> TopicActionItemRead:
    owner_name = None
    if item.owner_agent_id is not None:
        owner = getattr(item, "owner", None)
        if owner is None:
            owner = db.get(Agent, item.owner_agent_id)
        owner_name = owner.name if owner else None
    # 列表 / 决策读路径已批量预取 suggested，传入时跳过逐条 SQL（消除 N+1）。
    suggested_id, suggested_title = (
        suggested if suggested is not None else _suggest_linked_experiment(db, item)
    )
    # 同上：批量预取 phase 时直接用；否则单条 fallback 查询。
    if not linked_experiment_phase_provided:
        if item.linked_experiment_id is not None:
            exp = db.get(Experiment, item.linked_experiment_id)
            linked_experiment_phase = exp.phase if exp else None
        else:
            linked_experiment_phase = None
    return TopicActionItemRead(
        id=item.id,
        decision_id=item.decision_id,
        project_id=item.project_id,
        topic_id=item.topic_id,
        title=item.title,
        description=item.description,
        owner_agent_id=item.owner_agent_id,
        owner_name=owner_name,
        status=item.status,
        due_at=item.due_at,
        linked_experiment_id=item.linked_experiment_id,
        linked_experiment_phase=linked_experiment_phase,
        category=ActionItemCategory(item.category) if item.category else None,
        cancel_reason=item.cancel_reason,
        suggested_linked_experiment_id=suggested_id,
        suggested_linked_experiment_title=suggested_title,
        wake_count=item.wake_count,
        first_open_at=item.first_open_at,
        last_woken_at=item.last_woken_at,
        stale_at=item.stale_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )

def list_action_items(
    db: Session,
    project_id: uuid.UUID,
    *,
    owner_agent_id: uuid.UUID | None = None,
    status: TopicActionItemStatus | None = None,
    limit: int = 100,
) -> list[TopicActionItemRead]:
    get_project(db, project_id)
    stmt = (
        select(TopicActionItem)
        .where(TopicActionItem.project_id == project_id)
        .options(joinedload(TopicActionItem.owner))
        .order_by(TopicActionItem.updated_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if owner_agent_id is not None:
        stmt = stmt.where(TopicActionItem.owner_agent_id == owner_agent_id)
    if status is not None:
        stmt = stmt.where(TopicActionItem.status == status)
    items = list(db.scalars(stmt))
    suggested_map = _suggest_linked_experiments_batch(db, items)
    phase_map = _linked_experiment_phases_batch(db, items)
    return [
        _action_item_read(
            db,
            item,
            suggested=suggested_map.get(item.id),
            linked_experiment_phase=phase_map.get(item.id),
            linked_experiment_phase_provided=True,
        )
        for item in items
    ]


def _ensure_action_item_accessor(item: TopicActionItem, agent: Agent) -> None:
    """Caller must be the owner or an admin. Topic creator may be added later."""
    if item.owner_agent_id is None:
        # No owner assigned: admin-only is the safe default.
        if not is_admin(agent):
            raise ForbiddenError("Only admin can close an unassigned action item")
        return
    if item.owner_agent_id != agent.id and not is_admin(agent):
        raise ForbiddenError("Only the owner or admin can complete/cancel this action item")


def _complete_action_item_no_commit(
    db: Session,
    item: TopicActionItem,
    *,
    triggered_by: str = "manual",
) -> dict[str, Any]:
    """Move an open action item to ``done`` within the caller's transaction.

    Caller is responsible for:
      - 404 lookup (db.get on TopicActionItem)
      - Owner / admin access check (``_ensure_action_item_accessor``)
      - Final ``db.commit()``

    Returns a dict the caller can splice into a higher-level audit payload
    (e.g. ``cascaded_action_items`` on ``experiment.completed``).
    """
    if item.status != TopicActionItemStatus.open:
        raise ConflictError(f"Action item already {item.status.value}")
    prev_status = item.status
    item.status = TopicActionItemStatus.done
    audit_service.log_no_commit(
        db,
        action="action_item.completed",
        target_type="topic_action_item",
        target_id=item.id,
        project_id=item.project_id,
        summary=f"完成行动项「{item.title}」",
        payload={
            "action_item_id": str(item.id),
            "prev_status": prev_status.value,
            "new_status": item.status.value,
            "triggered_by": triggered_by,
        },
    )
    return {
        "action_item_id": str(item.id),
        "prev_status": prev_status.value,
        "new_status": item.status.value,
    }


def _action_item_source_topic(db: Session, item: TopicActionItem) -> Topic:
    topic = db.get(Topic, item.topic_id)
    if topic is None or topic.deleted_at is not None:
        raise NotFoundError("Action item source topic not found")
    return topic


def deliver_action_item_no_commit(
    db: Session,
    item: TopicActionItem,
    *,
    triggered_by: str = "deliver",
    agent_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Mark an open action item done when its source topic may be closed/archived."""
    if item.status != TopicActionItemStatus.open:
        raise ConflictError(f"Action item already {item.status.value}")
    topic = _action_item_source_topic(db, item)
    audit_service.log_no_commit(
        db,
        action="action_item.delivered",
        target_type="topic_action_item",
        target_id=item.id,
        agent_id=agent_id,
        project_id=item.project_id,
        summary=f"交付行动项「{item.title}」",
        payload={
            "action_item_id": str(item.id),
            "topic_id": str(topic.id),
            "topic_status": topic.status.value,
            "topic_archived": topic.archived_at is not None,
            "triggered_by": triggered_by,
        },
    )
    return _complete_action_item_no_commit(db, item, triggered_by=triggered_by)


def deliver_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
) -> TopicActionItemRead:
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    deliver_action_item_no_commit(db, item, triggered_by="deliver", agent_id=agent.id)
    db.commit()
    db.refresh(item)
    return _action_item_read(db, item)


def complete_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
    *,
    triggered_by: str = "manual",
) -> TopicActionItemRead:
    """Move an open action item to ``done``. Idempotent against non-open statuses (409)."""
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    _complete_action_item_no_commit(db, item, triggered_by=triggered_by)
    db.commit()
    db.refresh(item)
    return _action_item_read(db, item)


def mark_wake_sent_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
) -> TopicActionItemRead:
    """Bump wake_count + stamp last_woken_at + audit row (experiment B, plan §3).

    Access: owner or admin — same gate as ``complete`` / ``cancel`` so the
    runtime-waker CLI (driven by an admin agent) can escalate forgotten
    action_items whose owner is unresponsive. The transaction-internal
    helper does the mutation; this wrapper adds the access check + commit
    + read-back serialization.

    I5: also posts a wakeable notification to the assignee so the bump
    shows up in the owner's todos (B-1 / B-2 acceptance — wake is visible
    to the assignee independent of the audit log).
    """
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    try:
        action_item_service.mark_wake_sent(db, action_item_id)
    except ValueError as exc:
        # The I3 helper raises ValueError on non-open / unassigned items —
        # surface as ConflictError so the API returns 409 instead of 500.
        raise ConflictError(str(exc)) from exc
    # I5: notify the assignee. ``mark_wake_sent`` refreshes the identity-mapped
    # item in place, so ``item`` here reflects the new wake_count + last_woken_at
    # when we hand it to the notification helper.
    notification_service.notify_owner_action_item_wake(db, action_item=item)
    return _action_item_read(db, item)


def mark_stale_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
) -> TopicActionItemRead:
    """Stamp stale_at + write the ``action_item.stale`` audit row (plan §3).

    Access: admin only — the stale transition is a system escalation that
    should not be triggerable by the assignee themselves, since the whole
    point is that they have not responded to 4 wakes. ``admin_notified=True``
    and ``creator_audit_only=True`` are passed per plan §3 §6 (3c admin 优先
    + 6 creator 不 wake).

    I5: also posts a wakeable notification to every admin agent (excluding
    the owner) — plan §3 #2 (3c admin 优先). The creator is intentionally
    NOT in the recipient set (I6 audit-only policy): they can find the
    stale event via ``action_items`` listing or the audit history.
    """
    if not is_admin(agent):
        raise ForbiddenError("Only admin can mark an action item stale")
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    try:
        action_item_service.mark_stale(db, action_item_id)
    except ValueError as exc:
        # The audit-service helper refuses stale without a prior wake, and
        # refuses non-open items — both surface as ConflictError (409) so
        # the waker CLI can distinguish "retry later" from "5xx bug".
        raise ConflictError(str(exc)) from exc
    # I5: fan out to admin agents. No-op when the project has no admin
    # configured (per plan risk #3 this is a degraded state — the audit
    # row is still written, but no notification reaches a human).
    notification_service.notify_admin_action_item_stale(db, action_item=item)
    return _action_item_read(db, item)


def cancel_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
    payload: ActionItemCancel,
    *,
    triggered_by: str = "manual",
) -> TopicActionItemRead:
    """Move an open action item to ``cancelled``. Reason + category validated by schema."""
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    if item.status != TopicActionItemStatus.open:
        raise ConflictError(f"Action item already {item.status.value}")
    prev_status = item.status
    item.status = TopicActionItemStatus.cancelled
    item.cancel_reason = payload.reason
    if payload.category is not None:
        item.category = payload.category.value
    # 状态变更与 audit 行写在同一事务内（log_no_commit 只 flush），单次 commit；
    # audit 失败会连同状态变更一起回滚，避免「已取消但无审计」的脱钩。
    audit_service.log_no_commit(
        db,
        action="action_item.cancelled",
        target_type="topic_action_item",
        target_id=item.id,
        agent_id=agent.id,
        project_id=item.project_id,
        summary=f"取消行动项「{item.title}」",
        payload={
            "action_item_id": str(item.id),
            "prev_status": prev_status.value,
            "new_status": item.status.value,
            "cancel_reason": payload.reason,
            "category": (payload.category.value if payload.category else item.category),
            "triggered_by": triggered_by,
        },
    )
    db.commit()
    db.refresh(item)
    return _action_item_read(db, item)


def link_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    experiment_id: uuid.UUID,
    agent: Agent,
) -> TopicActionItemRead:
    """Attach an experiment to an open action item (``linked_experiment_id``).

    Idempotency: re-linking the same experiment is a no-op (returns the item).
    Relinking to a *different* experiment is rejected with 409 — to change link
    target, first unlink via service-internal flow (not currently exposed).

    Permissions: owner or admin (same as ``complete`` / ``cancel``).
    """
    from server.services.project_service import get_experiment

    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    if item.status != TopicActionItemStatus.open:
        raise ConflictError(f"Cannot link a {item.status.value} action item")

    experiment = get_experiment(db, experiment_id)
    if experiment.project_id != item.project_id:
        raise ConflictError("Action item and experiment belong to different projects")
    allowed_phases = {
        ExperimentPhase.running,
        ExperimentPhase.result_review,
        ExperimentPhase.done,
    }
    if experiment.phase not in allowed_phases:
        raise ConflictError(
            f"Cannot link experiment in phase {experiment.phase.value}; "
            "must be running, result_review, or done"
        )

    if item.linked_experiment_id == experiment_id:
        return _action_item_read(db, item)
    if item.linked_experiment_id is not None:
        raise ConflictError(
            "Action item is already linked to a different experiment; "
            "unlink first (not yet supported via CLI)"
        )

    item.linked_experiment_id = experiment_id
    db.commit()
    db.refresh(item)
    audit_service.log(
        db,
        action="action_item.linked",
        target_type="topic_action_item",
        target_id=item.id,
        agent_id=agent.id,
        project_id=item.project_id,
        summary=f"关联行动项「{item.title}」到实验「{experiment.title}」",
        payload={
            "action_item_id": str(item.id),
            "experiment_id": str(experiment_id),
            "triggered_by": "manual",
        },
    )
    return _action_item_read(db, item)

