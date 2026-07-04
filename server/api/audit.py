import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import (
    AuditLogRead,
)
from server.services import audit_service
from server.services import permissions as perm

audit_router = APIRouter(tags=["audit"])


@audit_router.get("/audit", response_model=list[AuditLogRead])
def list_audit_for_target(
    target_type: str = Query(...),
    target_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[AuditLogRead]:
    perm.ensure_audit_target_access(db, agent, target_type, target_id)
    return audit_service.query_by_target(db, target_type, target_id)


@audit_router.get("/admin/audit", response_model=list[AuditLogRead])
def list_audit_global(
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[AuditLogRead]:
    perm.require_admin(agent)
    items, total = audit_service.query_all(db, page=page, page_size=page_size)
    response.headers["X-Total-Count"] = str(total)
    return items
