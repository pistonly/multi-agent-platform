"""Topic resolve + decision read helpers.

拆分自 ``topic_service.py``。本模块负责：
- ``resolve_topic``（决策 upsert + action_item 二次约束）
- ``topic_decision_read`` / ``_load_decision`` / ``list_project_decisions``

Action-item 序列化委托 ``topic_action_item_ops``；
``topic_service`` re-exports 保持 import 兼容。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from map_types.enums import AgentRole, TopicActionItemStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import Agent, Experiment, Topic, TopicActionItem, TopicDecision
from server.domain.schemas import TopicDecisionRead, TopicResolve
from server.services import audit_service
from server.services._lookups import get_project
from server.services.errors import NotFoundError
from server.services.topic_action_item_ops import (
    _action_item_read,
    _linked_experiment_phases_batch,
    _suggest_linked_experiments_batch,
)
from server.services.topic_helpers import _get_topic


def topic_decision_read(db: Session, decision: TopicDecision) -> TopicDecisionRead:
    author_name = None
    author = getattr(decision, "author", None)
    if author is None:
        author = db.get(Agent, decision.author_agent_id)
    if author is not None:
        author_name = author.name

    topic_title = None
    topic = getattr(decision, "topic", None)
    if topic is None:
        topic = db.get(Topic, decision.topic_id)
    if topic is not None:
        topic_title = topic.title

    suggested_map = _suggest_linked_experiments_batch(db, list(decision.action_items))
    phase_map = _linked_experiment_phases_batch(db, list(decision.action_items))
    return TopicDecisionRead(
        id=decision.id,
        project_id=decision.project_id,
        topic_id=decision.topic_id,
        topic_title=topic_title,
        author_agent_id=decision.author_agent_id,
        author_name=author_name,
        decision=decision.decision,
        rationale=decision.rationale,
        rejected_options=decision.rejected_options,
        open_questions=decision.open_questions,
        no_decision_reason=decision.no_decision_reason,
        action_items=[
            _action_item_read(
                db,
                item,
                suggested=suggested_map.get(item.id),
                linked_experiment_phase=phase_map.get(item.id),
                linked_experiment_phase_provided=True,
            )
            for item in decision.action_items
        ],
        created_at=decision.created_at,
        updated_at=decision.updated_at,
    )


def _load_decision(db: Session, topic_id: uuid.UUID) -> TopicDecision | None:
    stmt = (
        select(TopicDecision)
        .where(TopicDecision.topic_id == topic_id)
        .options(
            joinedload(TopicDecision.author),
            joinedload(TopicDecision.topic),
            joinedload(TopicDecision.action_items).joinedload(TopicActionItem.owner),
        )
    )
    return db.execute(stmt).unique().scalar_one_or_none()
def resolve_topic(
    db: Session,
    topic_id: uuid.UUID,
    author: Agent,
    payload: TopicResolve,
) -> TopicDecision:
    topic = _get_topic(db, topic_id)
    decision = db.scalar(select(TopicDecision).where(TopicDecision.topic_id == topic_id))
    if decision is None:
        decision = TopicDecision(
            project_id=topic.project_id,
            topic_id=topic.id,
            author_agent_id=author.id,
        )
        db.add(decision)
        db.flush()
    else:
        decision.author_agent_id = author.id

    decision.decision = payload.decision
    decision.rationale = payload.rationale
    decision.rejected_options = payload.rejected_options
    decision.open_questions = payload.open_questions
    decision.no_decision_reason = payload.no_decision_reason

    # --- 二次约束: 旧项不在新 payload 中 → 等价显式 done (A1 resolve idempotency) -
    # Up until v0.x the resolver did ``db.execute(delete(TopicActionItem))`` and
    # re-inserted from payload, which made every re-resolve a destructive
    # delete-and-rebuild and made ``status=done`` impossible to persist. The new
    # rule is: items not present in the new payload (by id) are auto-closed as
    # ``done``; items present are upserted by id (fields updated, status
    # preserved); items with no id are inserted as open.
    existing = list(
        db.scalars(select(TopicActionItem).where(TopicActionItem.decision_id == decision.id))
    )
    existing_by_id = {item.id: item for item in existing}
    new_payload_ids: set[uuid.UUID] = {
        item.id for item in payload.action_items if item.id is not None
    }

    audit_entries: list[dict[str, Any]] = []
    for old_item in existing:
        if old_item.id in new_payload_ids:
            continue
        # 旧项不在新 payload：若仍为 open，等价显式 done（写 audit；已 closed 项保持原状）。
        if old_item.status == TopicActionItemStatus.open:
            prev_status = old_item.status
            old_item.status = TopicActionItemStatus.done
            old_item.updated_at = datetime.now(UTC)
            audit_entries.append(
                {
                    "action": "action_item.completed",
                    "target_id": old_item.id,
                    "payload": {
                        "action_item_id": str(old_item.id),
                        "prev_status": prev_status.value,
                        "new_status": old_item.status.value,
                        "triggered_by": "topic_resolve",
                    },
                }
            )

    for item_payload in payload.action_items:
        if item_payload.owner_agent_id is not None:
            owner = db.get(Agent, item_payload.owner_agent_id)
            if owner is None or (owner.project_id != topic.project_id and owner.role != AgentRole.admin):
                raise NotFoundError("Action item owner agent not found")
        if item_payload.linked_experiment_id is not None:
            experiment = db.get(Experiment, item_payload.linked_experiment_id)
            if experiment is None or experiment.deleted_at is not None or experiment.project_id != topic.project_id:
                raise NotFoundError("Linked experiment not found")
        if item_payload.id is not None and item_payload.id in existing_by_id:
            old_item = existing_by_id[item_payload.id]
            old_item.title = item_payload.title
            old_item.description = item_payload.description
            old_item.owner_agent_id = item_payload.owner_agent_id
            old_item.due_at = item_payload.due_at
            old_item.linked_experiment_id = item_payload.linked_experiment_id
            old_item.topic_id = topic.id
            if item_payload.category is not None:
                old_item.category = item_payload.category.value
        else:
            # Stamp first_open_at at creation so the waker's T+24h / T+72h
            # escalation timer starts immediately on resolve. Plan §2: open
            # transitions are the moment the timer anchors to. Existing open
            # items are backfilled by alembic migration 027.
            db.add(
                TopicActionItem(
                    decision_id=decision.id,
                    project_id=topic.project_id,
                    topic_id=topic.id,
                    title=item_payload.title,
                    description=item_payload.description,
                    owner_agent_id=item_payload.owner_agent_id,
                    status=TopicActionItemStatus.open,
                    due_at=item_payload.due_at,
                    linked_experiment_id=item_payload.linked_experiment_id,
                    category=item_payload.category.value if item_payload.category else None,
                    first_open_at=datetime.now(UTC),
                )
            )

    # Audit 与状态变更同事务：audit_entries 收集的「旧项等价 done」事件用
    # log_no_commit 累积，与上面的 decision / action_item 变更在单次 commit 内
    # 一起落库。commit 失败则全部回滚——不再出现「decision 已存但 audit 缺失」。
    for entry in audit_entries:
        audit_service.log_no_commit(
            db,
            action=entry["action"],
            target_type="topic_action_item",
            target_id=entry["target_id"],
            agent_id=author.id,
            project_id=topic.project_id,
            summary="resolve 二次约束关闭 action_item",
            payload=entry["payload"],
        )
    db.commit()

    loaded = _load_decision(db, topic.id)
    if loaded is None:
        raise NotFoundError("Topic decision not found")
    return loaded


def list_project_decisions(
    db: Session,
    project_id: uuid.UUID,
    *,
    limit: int = 20,
) -> list[TopicDecisionRead]:
    get_project(db, project_id)
    rows = list(
        db.scalars(
            select(TopicDecision)
            .where(TopicDecision.project_id == project_id)
            .options(
                joinedload(TopicDecision.author),
                joinedload(TopicDecision.topic),
                joinedload(TopicDecision.action_items).joinedload(TopicActionItem.owner),
            )
            .order_by(TopicDecision.updated_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        .unique()
    )
    return [topic_decision_read(db, row) for row in rows]

