import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import (
    WebhookCreate,
    WebhookCreateResponse,
    WebhookDeliveryRead,
    WebhookRead,
    WebhookUpdate,
)
from server.services import permissions as perm
from server.services import webhook_service

webhooks_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@webhooks_router.post("", response_model=WebhookCreateResponse, status_code=status.HTTP_201_CREATED)
def create_webhook(
    payload: WebhookCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> WebhookCreateResponse:
    perm.require_admin(agent)
    webhook, secret = webhook_service.create_webhook(db, payload)
    return WebhookCreateResponse(**WebhookRead.model_validate(webhook).model_dump(), secret=secret)


@webhooks_router.get("", response_model=list[WebhookRead])
def list_webhooks(
    response: Response,
    project_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[WebhookRead]:
    # T41：page/page_size + X-Total-Count，与 topics/audit 列表风格一致。
    perm.require_admin(agent)
    webhooks, total = webhook_service.list_webhooks(
        db, project_id=project_id, page=page, page_size=page_size
    )
    response.headers["X-Total-Count"] = str(total)
    return [WebhookRead.model_validate(w) for w in webhooks]


@webhooks_router.patch("/{webhook_id}", response_model=WebhookRead)
def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> WebhookRead:
    perm.require_admin(agent)
    webhook = webhook_service.update_webhook(db, webhook_id, payload)
    return WebhookRead.model_validate(webhook)


@webhooks_router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    perm.require_admin(agent)
    webhook_service.delete_webhook(db, webhook_id)


@webhooks_router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryRead])
def list_webhook_deliveries(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[WebhookDeliveryRead]:
    perm.require_admin(agent)
    deliveries = webhook_service.list_deliveries(db, webhook_id)
    return [WebhookDeliveryRead.model_validate(d) for d in deliveries]
