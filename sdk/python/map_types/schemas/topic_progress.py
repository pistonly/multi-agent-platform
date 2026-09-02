"""Per-agent topic work-item projection schemas."""

import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field

from .base import ORMModel


class TopicProgressCommentRead(ORMModel):
    id: uuid.UUID
    author_agent_id: uuid.UUID
    author_name: str | None = None
    parent_comment_id: uuid.UUID | None
    body: str
    excerpt: str
    created_at: datetime


class TopicWorkItemRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    priority: str
    topic_id: uuid.UUID
    topic_title: str
    source_comment_id: uuid.UUID | None = None
    thread_root_id: uuid.UUID | None = None
    required_agent_id: uuid.UUID
    reason: str
    idempotency_key: str
    clear_action: str
    excerpt: str
    suggested_command: str | None = None
    created_at: datetime
    discussion_round: str | None = None
    stale_since: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("stale_since", "advance_round_pending_since"),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def advance_round_pending_since(self) -> datetime | None:
        """DEPRECATED alias for stale_since."""
        return self.stale_since


class TopicProgressItemRead(BaseModel):
    topic_id: uuid.UUID
    topic_title: str
    discussion_round: str
    last_comment_author_agent_id: uuid.UUID | None = None
    last_comment_author_name: str | None = None
    my_last_comment_id: uuid.UUID | None = None
    new_comments: list[TopicProgressCommentRead] = Field(default_factory=list)
    new_comment_count: int = 0
    work_items: list[TopicWorkItemRead] = Field(default_factory=list)


class TopicProgressListRead(BaseModel):
    items: list[TopicProgressItemRead] = Field(default_factory=list)
    total: int = 0
