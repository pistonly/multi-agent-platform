"""Platform feedback schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from map_types.enums import FeedbackCategory, FeedbackStatus

from .base import ORMModel

# --- Platform Feedback ---


class PlatformFeedbackCreate(BaseModel):
    """A free-text feedback entry any authenticated agent may submit.

    `project_id` is an optional source-context hint (the project the agent was
    working in); it is NOT an access boundary — feedback is platform-wide.
    """

    body: str = Field(min_length=1, max_length=20000)
    project_id: uuid.UUID | None = None
    category: FeedbackCategory | None = None
    metadata: dict | None = None


class PlatformFeedbackUpdate(BaseModel):
    """Admin-only triage fields."""

    status: FeedbackStatus | None = None
    category: FeedbackCategory | None = None
    archived: bool | None = None


class PlatformFeedbackRead(ORMModel):
    id: uuid.UUID
    author_agent_id: uuid.UUID
    author_name: str | None = None
    project_id: uuid.UUID | None
    body: str
    category: FeedbackCategory | None
    status: FeedbackStatus
    metadata_json: dict | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
