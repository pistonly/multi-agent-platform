import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import NotificationRead
from server.services import notification_service

notifications_router = APIRouter(prefix="/notifications", tags=["notifications"])


@notifications_router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_notification_read(
    notification_id: uuid.UUID,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> NotificationRead:
    notification = notification_service.mark_read(db, agent, notification_id)
    return NotificationRead.model_validate(notification)
