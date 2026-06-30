import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from map_types.enums import FeedbackCategory, FeedbackStatus
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import (
    PlatformFeedbackCreate,
    PlatformFeedbackRead,
    PlatformFeedbackUpdate,
)
from server.services import permissions as perm
from server.services import platform_feedback_service as svc

feedback_router = APIRouter(prefix="/feedback", tags=["feedback"])


@feedback_router.post("", response_model=PlatformFeedbackRead, status_code=status.HTTP_201_CREATED)
def submit_feedback(
    payload: PlatformFeedbackCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlatformFeedbackRead:
    """Any authenticated agent may submit feedback. No project boundary enforced."""
    return svc.create_feedback(db, agent, payload)


@feedback_router.get("", response_model=list[PlatformFeedbackRead])
def list_feedback(
    response: Response,
    feedback_status: FeedbackStatus | None = Query(default=None, alias="status"),
    category: FeedbackCategory | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[PlatformFeedbackRead]:
    perm.require_admin(agent)
    items, total = svc.list_feedback(
        db,
        status=feedback_status,
        category=category,
        project_id=project_id,
        page=page,
        page_size=page_size,
        include_archived=include_archived,
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@feedback_router.get("/{feedback_id}", response_model=PlatformFeedbackRead)
def get_feedback(
    feedback_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlatformFeedbackRead:
    perm.require_admin(agent)
    return svc.get_feedback_read(db, feedback_id)


@feedback_router.patch("/{feedback_id}", response_model=PlatformFeedbackRead)
def update_feedback(
    feedback_id: uuid.UUID,
    payload: PlatformFeedbackUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlatformFeedbackRead:
    perm.require_admin(agent)
    return svc.update_feedback(db, feedback_id, payload)
