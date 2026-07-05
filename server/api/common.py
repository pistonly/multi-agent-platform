import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.api.background_tasks import get_background_tasks
from server.domain.models import Agent
from server.services import audit_service, notification_service, webhook_service
from server.services.errors import ConflictError, ForbiddenError, NotFoundError, StateTransitionError, UnauthorizedError


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
    notify: bool = True,
) -> None:
    """记录审计日志并扇出副作用（notification + webhook）。

    副作用顺序：audit → notification → webhook。
    audit 总是执行；notification 仅在 ``notify=True`` 时执行；
    webhook 仅在 ``event`` 非空时执行，投递失败不影响主流程。
    """
    _log_audit(
        db,
        agent=agent,
        action=action,
        target_type=target_type,
        target_id=target_id,
        project_id=project_id,
        summary=summary,
    )
    if event is None:
        return
    payload = event_payload or {}
    if notify:
        _dispatch_notifications(
            db,
            project_id=project_id,
            actor_id=agent.id,
            event=event,
            summary=summary or event,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )
    _dispatch_webhooks(db, event=event, payload=payload, project_id=project_id)


def _log_audit(
    db: Session,
    *,
    agent: Agent,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    summary: str | None,
) -> None:
    audit_service.log(
        db,
        agent_id=agent.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        project_id=project_id,
        summary=summary,
    )


def _dispatch_notifications(
    db: Session,
    *,
    project_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict,
) -> None:
    notification_service.enqueue_from_event(
        db,
        project_id=project_id,
        actor_id=actor_id,
        event=event,
        summary=summary,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )


def _dispatch_webhooks(
    db: Session,
    *,
    event: str,
    payload: dict,
    project_id: uuid.UUID | None,
) -> None:
    delivery_ids = webhook_service.enqueue_event_deliveries(
        db, event, payload, project_id
    )
    background_tasks = get_background_tasks()
    if background_tasks is not None:
        for delivery_id in delivery_ids:
            background_tasks.add_task(webhook_service.deliver_delivery, delivery_id)
    else:
        webhook_service.deliver_deliveries(db, delivery_ids)


