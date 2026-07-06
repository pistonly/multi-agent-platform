from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, joinedload

from server.db import session as db_session_module
from server.domain.models import Webhook, WebhookDelivery
from server.domain.schemas import WebhookCreate, WebhookUpdate
from server.services.errors import NotFoundError


logger = logging.getLogger(__name__)

# 单次投递最多尝试次数（含首次）。重试间隔为 2 ** (attempt-1) 秒，即 1s / 2s。
# 总最坏阻塞时长 ≈ 1 + 2 = 3s，仍短于 httpx 单次 5s timeout，可控。
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds


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


def _summarize_error(status_code: int | None, exc: BaseException | None) -> str | None:
    """Compact one-line error summary for ``last_error``."""
    if exc is not None:
        return f"{type(exc).__name__}: {exc}"[:500]
    if status_code is None:
        return None
    if 200 <= status_code < 300:
        return None
    return f"HTTP {status_code}"


def _perform_delivery(
    db: Session,
    delivery_id: uuid.UUID,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
) -> None:
    """Deliver one payload with bounded retry.

    Strategy:
    - 2xx → success, stop.
    - 4xx (except 408 / 429) → client error, do not retry (per common webhook
      convention; the receiver explicitly rejected the payload).
    - Network error / 408 / 429 / 5xx → retry with exponential backoff
      (``backoff_base * 2 ** (attempt-1)``), up to ``max_attempts``.

    Each attempt updates ``attempts`` / ``last_attempt_at`` / ``status_code`` /
    ``last_error`` so admin can inspect progress in the deliveries list. The
    function is sync because ``deliver_delivery`` runs in a BackgroundTask and
    we accept the small worst-case latency (~3 retries × 5s timeout) for the
    sake of reliable delivery.
    """
    delivery = db.scalar(
        select(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .options(joinedload(WebhookDelivery.webhook))
    )
    if delivery is None or delivery.webhook is None:
        return

    webhook = delivery.webhook
    body = json.dumps(delivery.payload, default=str, ensure_ascii=False).encode("utf-8")
    signature = (
        "sha256="
        + hmac.new(webhook.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )

    last_status: int | None = None
    last_exc: BaseException | None = None
    success = False
    attempts = 0

    # Retry only on network errors and transient status codes (timeout / too many
    # requests / server errors). 4xx (except 408/429) means the receiver rejected
    # the payload, so retrying won't help.
    transient_statuses = {408, 425, 429, 500, 502, 503, 504}

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        last_exc = None
        try:
            last_status = _http_post(webhook.url, body, signature)
            delivery.status_code = last_status
            if 200 <= last_status < 300:
                success = True
                break
            if last_status not in transient_statuses:
                # 4xx (non-transient): client error, no point retrying.
                break
        except Exception as exc:  # noqa: BLE001 — external HTTP, broad catch intended
            last_exc = exc
            last_status = None
            delivery.status_code = None
            logger.warning(
                "webhook delivery %s attempt %d/%d failed: %s",
                delivery_id,
                attempt,
                max_attempts,
                exc,
            )

        # Persist progress so admin can watch attempts tick up even mid-retry.
        delivery.attempts = attempts
        delivery.last_attempt_at = datetime.now(UTC)
        delivery.last_error = _summarize_error(last_status, last_exc)
        db.flush()

        if attempt < max_attempts:
            sleep_for = backoff_base * (2 ** (attempt - 1))
            time.sleep(sleep_for)

    delivery.attempts = attempts
    delivery.success = success
    delivery.last_attempt_at = datetime.now(UTC)
    delivery.last_error = _summarize_error(last_status, last_exc)
    if success:
        logger.info(
            "webhook delivery %s succeeded after %d attempt(s)",
            delivery_id,
            attempts,
        )
    else:
        logger.warning(
            "webhook delivery %s failed after %d attempt(s): %s",
            delivery_id,
            attempts,
            delivery.last_error,
        )


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
