from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, joinedload

from server.db import session as db_session_module
from server.domain.models import Webhook, WebhookDelivery
from server.domain.schemas import WebhookCreate, WebhookUpdate
from server.services.errors import NotFoundError


def _http_post(url: str, body: bytes, signature: str) -> int:
    """Send a webhook payload. Overridable in tests via monkeypatch."""
    resp = httpx.post(
        url,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-MAP-Signature": signature,
        },
        timeout=5.0,
    )
    return resp.status_code


def create_webhook(db: Session, payload: WebhookCreate) -> tuple[Webhook, str]:
    secret = secrets.token_urlsafe(32)
    webhook = Webhook(
        project_id=payload.project_id,
        url=payload.url,
        events=payload.events,
        secret=secret,
        active=True,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook, secret


def list_webhooks(db: Session, project_id: uuid.UUID | None = None) -> list[Webhook]:
    stmt = select(Webhook).order_by(Webhook.created_at.desc())
    if project_id is not None:
        stmt = stmt.where((Webhook.project_id == project_id) | (Webhook.project_id.is_(None)))
    return list(db.scalars(stmt))


def get_webhook(db: Session, webhook_id: uuid.UUID) -> Webhook:
    webhook = db.get(Webhook, webhook_id)
    if webhook is None:
        raise NotFoundError("Webhook not found")
    return webhook


def update_webhook(db: Session, webhook_id: uuid.UUID, payload: WebhookUpdate) -> Webhook:
    webhook = get_webhook(db, webhook_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(webhook, key, value)
    db.commit()
    db.refresh(webhook)
    return webhook


def delete_webhook(db: Session, webhook_id: uuid.UUID) -> None:
    webhook = get_webhook(db, webhook_id)
    db.execute(delete(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id))
    db.delete(webhook)
    db.commit()


def list_deliveries(db: Session, webhook_id: uuid.UUID) -> list[WebhookDelivery]:
    get_webhook(db, webhook_id)
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
    )
    return list(db.scalars(stmt))


def _matching_webhooks(db: Session, project_id: uuid.UUID | None, event: str) -> list[Webhook]:
    stmt = select(Webhook).where(Webhook.active.is_(True))
    if project_id is not None:
        stmt = stmt.where(or_(Webhook.project_id == project_id, Webhook.project_id.is_(None)))
    matched: list[Webhook] = []
    for wh in db.scalars(stmt):
        if wh.events and event not in wh.events:
            continue
        matched.append(wh)
    return matched


def enqueue_event_deliveries(
    db: Session,
    event: str,
    payload: dict,
    project_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Create pending delivery rows; HTTP happens in deliver_delivery()."""
    body_obj = {"event": event, "payload": payload}
    delivery_ids: list[uuid.UUID] = []
    for webhook in _matching_webhooks(db, project_id, event):
        delivery = WebhookDelivery(
            webhook_id=webhook.id,
            event=event,
            payload=body_obj,
            attempts=0,
            success=False,
        )
        db.add(delivery)
        db.flush()
        delivery_ids.append(delivery.id)
    if delivery_ids:
        db.commit()
    return delivery_ids


def _perform_delivery(db: Session, delivery_id: uuid.UUID) -> None:
    delivery = db.scalar(
        select(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .options(joinedload(WebhookDelivery.webhook))
    )
    if delivery is None or delivery.webhook is None:
        return

    webhook = delivery.webhook
    body_obj = delivery.payload
    body = json.dumps(body_obj, default=str, ensure_ascii=False).encode("utf-8")
    signature = (
        "sha256="
        + hmac.new(webhook.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    try:
        status_code = _http_post(webhook.url, body, signature)
        delivery.status_code = status_code
        delivery.success = 200 <= status_code < 300
    except Exception:
        delivery.status_code = None
        delivery.success = False
    delivery.attempts = 1
    delivery.last_attempt_at = datetime.now(UTC)


def deliver_deliveries(db: Session, delivery_ids: list[uuid.UUID]) -> None:
    """Deliver using the caller's DB session (sync fallback / tests)."""
    if not delivery_ids:
        return
    for delivery_id in delivery_ids:
        _perform_delivery(db, delivery_id)
    db.commit()


def deliver_delivery(delivery_id: uuid.UUID) -> None:
    """Perform HTTP delivery in a background task (uses its own DB session)."""
    db = db_session_module.SessionLocal()
    try:
        _perform_delivery(db, delivery_id)
        db.commit()
    finally:
        db.close()


def deliver_event(
    db: Session,
    event: str,
    payload: dict,
    project_id: uuid.UUID | None,
) -> None:
    """Synchronous delivery (tests / fallback when no BackgroundTasks bound)."""
    delivery_ids = enqueue_event_deliveries(db, event, payload, project_id)
    deliver_deliveries(db, delivery_ids)
