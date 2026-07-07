import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import (
    GlobalStatusRead,
)
from server.services import permissions as perm
from server.services import status_service
from server.services.errors import ForbiddenError

status_router = APIRouter(prefix="/status", tags=["status"])


@status_router.get("", response_model=GlobalStatusRead)
def get_global_status(
    project_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> GlobalStatusRead:
    if project_id is None:
        if not perm.is_admin(agent):
            if agent.project_id is None:
                raise ForbiddenError("Agent is not bound to a project")
            project_id = agent.project_id
        else:
            return status_service.get_global_status(db, project_id=None)
    perm.ensure_project_access(agent, project_id)
    return status_service.get_global_status(db, project_id=project_id)
