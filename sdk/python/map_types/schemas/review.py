"""Experiment review schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from map_types.enums import ResolutionReason, ReviewArchivedReason, ReviewItemKind, ReviewItemStatus, ReviewSubstituteKind

from .base import ORMModel



# --- Review ---


class ReviewCreate(BaseModel):
    reasonable_items: list[str] = Field(default_factory=list)
    unreasonable_items: list[str] = Field(default_factory=list)
    substitute_reason: str | None = Field(
        default=None,
        min_length=1,
        description="Required when admin submits a substitute review (admin_for_others).",
    )


class ReviewItemRead(ORMModel):
    id: uuid.UUID
    review_id: uuid.UUID
    kind: ReviewItemKind
    content: str
    status: ReviewItemStatus | None
    last_resolution_reason: ResolutionReason | None = None
    created_at: datetime
    updated_at: datetime
    waived_reason: str | None = Field(
        default=None,
        description=(
            "Populated server-side from the latest accept-result verdict_file "
            "where verdict='waived' for this item. Read-only convenience field "
            "for participant-facing UIs; the source of truth is "
            "experiment_logs.metadata_json.verdict_file."
        ),
    )


class ReviewItemUpdate(BaseModel):
    status: ReviewItemStatus


class ReviewRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    reviewer_agent_id: uuid.UUID
    plan_version: int
    substitute_kind: ReviewSubstituteKind = ReviewSubstituteKind.none
    created_at: datetime
    archived_at: datetime | None = None
    archived_reason: ReviewArchivedReason | None = None
    items: list[ReviewItemRead] = Field(default_factory=list)
