"""Migrate or deliver open action items tied to closed/archived topics."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import AuditLog, Topic, TopicActionItem, TopicActionItemStatus, TopicStatus
from server.services import audit_service, topic_service


@dataclass(frozen=True)
class MigrationResult:
    action_item_id: uuid.UUID
    strategy: str
    from_topic_id: uuid.UUID
    to_topic_id: uuid.UUID | None
    dry_run: bool
    skipped: bool
    reason: str | None = None


def _idempotency_key(item_id: uuid.UUID) -> str:
    return f"action_item_migrate:{item_id}"


def migration_already_applied(db: Session, item_id: uuid.UUID) -> bool:
    key = _idempotency_key(item_id)
    rows = db.scalars(
        select(AuditLog).where(
            AuditLog.action == "action_item.migrated",
            AuditLog.target_id == item_id,
        )
    ).all()
    return any((row.payload_json or {}).get("idempotency_key") == key for row in rows)


def list_stale_open_action_items(db: Session) -> list[TopicActionItem]:
    """Open items whose source topic is closed or archived (not soft-deleted)."""
    return list(
        db.scalars(
            select(TopicActionItem)
            .join(Topic, TopicActionItem.topic_id == Topic.id)
            .options(joinedload(TopicActionItem.topic))
            .where(
                TopicActionItem.status == TopicActionItemStatus.open,
                Topic.deleted_at.is_(None),
                or_(
                    Topic.status == TopicStatus.closed,
                    Topic.archived_at.isnot(None),
                ),
            )
            .order_by(TopicActionItem.updated_at.desc())
        )
    )


def migrate_action_item(
    db: Session,
    item: TopicActionItem,
    *,
    migrate_to_topic_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> MigrationResult:
    from_topic_id = item.topic_id
    if migration_already_applied(db, item.id):
        return MigrationResult(
            action_item_id=item.id,
            strategy="skip",
            from_topic_id=from_topic_id,
            to_topic_id=None,
            dry_run=dry_run,
            skipped=True,
            reason="already_migrated",
        )

    if migrate_to_topic_id is not None:
        target = db.get(Topic, migrate_to_topic_id)
        if target is None or target.deleted_at is not None:
            raise ValueError(f"Target topic {migrate_to_topic_id} not found")
        strategy = "migrate_to"
        to_topic_id = migrate_to_topic_id
        if not dry_run:
            item.topic_id = migrate_to_topic_id
            audit_service._log_no_commit(
                db,
                action="action_item.migrated",
                target_type="topic_action_item",
                target_id=item.id,
                agent_id=agent_id,
                project_id=item.project_id,
                summary=f"迁移行动项「{item.title}」到新话题",
                payload={
                    "idempotency_key": _idempotency_key(item.id),
                    "from_topic_id": str(from_topic_id),
                    "to_topic_id": str(to_topic_id),
                    "strategy": strategy,
                },
            )
            db.commit()
        return MigrationResult(
            action_item_id=item.id,
            strategy=strategy,
            from_topic_id=from_topic_id,
            to_topic_id=to_topic_id,
            dry_run=dry_run,
            skipped=False,
        )

    strategy = "cascade_backlog"
    if not dry_run:
        suggested_id, _ = topic_service._suggest_linked_experiment(db, item)
        if suggested_id is not None:
            item.linked_experiment_id = suggested_id
        topic_service.deliver_action_item_no_commit(
            db,
            item,
            triggered_by="migration:cascade_backlog",
        )
        audit_service._log_no_commit(
            db,
            action="action_item.migrated",
            target_type="topic_action_item",
            target_id=item.id,
            agent_id=agent_id,
            project_id=item.project_id,
            summary=f"迁移关闭行动项「{item.title}」（cascade backlog）",
            payload={
                "idempotency_key": _idempotency_key(item.id),
                "from_topic_id": str(from_topic_id),
                "to_topic_id": None,
                "strategy": strategy,
                "linked_experiment_id": str(item.linked_experiment_id)
                if item.linked_experiment_id
                else None,
            },
        )
        db.commit()
    return MigrationResult(
        action_item_id=item.id,
        strategy=strategy,
        from_topic_id=from_topic_id,
        to_topic_id=None,
        dry_run=dry_run,
        skipped=False,
    )


def migrate_stale_open_action_items(
    db: Session,
    *,
    migrate_to_topic_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> list[MigrationResult]:
    results: list[MigrationResult] = []
    for item in list_stale_open_action_items(db):
        try:
            results.append(
                migrate_action_item(
                    db,
                    item,
                    migrate_to_topic_id=migrate_to_topic_id,
                    agent_id=agent_id,
                    dry_run=dry_run,
                )
            )
        except Exception as exc:
            if not dry_run:
                db.rollback()
                audit_service.log(
                    db,
                    action="action_item.migration_failed",
                    target_type="topic_action_item",
                    target_id=item.id,
                    agent_id=agent_id,
                    project_id=item.project_id,
                    summary=f"迁移行动项失败「{item.title}」",
                    payload={
                        "action_item_id": str(item.id),
                        "from_topic_id": str(item.topic_id),
                        "error": str(exc),
                    },
                )
            raise
    return results
