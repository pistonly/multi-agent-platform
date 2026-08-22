"""Webhook schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .base import ORMModel

# --- Webhook ---


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    events: list[str] = Field(default_factory=list)
    project_id: uuid.UUID | None = None


class WebhookUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    events: list[str] | None = None
    active: bool | None = None


class WebhookRead(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class WebhookCreateResponse(WebhookRead):
    secret: str


class WebhookDeliveryRead(ORMModel):
    id: uuid.UUID
    webhook_id: uuid.UUID
    event: str
    payload: dict[str, Any]
    status_code: int | None
    attempts: int
    success: bool
    last_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime
