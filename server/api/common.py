import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.api.background_tasks import get_background_tasks
from server.domain.models import Agent
from server.services import audit_service, webhook_service
from server.services.errors import ConflictError, ForbiddenError, NotFoundError, StateTransitionError, UnauthorizedError

def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, UnauthorizedError):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    if isinstance(exc, ForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, StateTransitionError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    raise exc


def emit(
    db: Session,
    agent: Agent,
    *,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    summary: str | None = None,
    event: str | None = None,
    event_payload: dict | None = None,
) -> None:
    """记录审计日志并（可选）触发 webhook 投递；投递失败不影响主流程。"""
    audit_service.log(
        db,
        agent_id=agent.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        project_id=project_id,
        summary=summary,
    )
    if event is not None:
        delivery_ids = webhook_service.enqueue_event_deliveries(
            db, event, event_payload or {}, project_id
        )
        background_tasks = get_background_tasks()
        if background_tasks is not None:
            for delivery_id in delivery_ids:
                background_tasks.add_task(webhook_service.deliver_delivery, delivery_id)
        else:
            webhook_service.deliver_deliveries(db, delivery_ids)


