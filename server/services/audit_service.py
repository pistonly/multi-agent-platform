from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import AuditLog
from server.domain.schemas import AuditLogRead


def log(
    db: Session,
    *,
    action: str,
    target_type: str,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        agent_id=agent_id,
        project_id=project_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        payload_json=payload,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def query_by_target(
    db: Session,
    target_type: str,
    target_id: uuid.UUID,
) -> list[AuditLogRead]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.target_type == target_type, AuditLog.target_id == target_id)
        .order_by(AuditLog.created_at.desc())
    )
    return [AuditLogRead.model_validate(row) for row in db.scalars(stmt)]


def query_all(db: Session, *, page: int = 1, page_size: int = 50) -> tuple[list[AuditLogRead], int]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return [AuditLogRead.model_validate(row) for row in items], total
