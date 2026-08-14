"""Experiment plan file-reference schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from .base import ORMModel



# --- Plan ---


class PlanInput(BaseModel):
    content_md: str | None = Field(default=None, min_length=1)
    change_note: str | None = Field(default=None, max_length=1024)
    file_path: str | None = None

    @model_validator(mode="after")
    def _require_content_or_path(self) -> "PlanInput":
        if not self.content_md and not self.file_path:
            raise ValueError("Either content_md or file_path must be provided")
        return self


class PlanRevise(BaseModel):
    content_md: str = Field(min_length=1)
    change_note: str | None = Field(default=None, max_length=1024)
    addressed_item_ids: list[uuid.UUID] = Field(default_factory=list)


class PlanVersionRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    version: int
    content_md: str
    author_agent_id: uuid.UUID
    change_note: str | None
    created_at: datetime
