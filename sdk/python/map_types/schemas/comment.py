"""Legacy comment schemas (pre file-reference)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from map_types.enums import CommentAnchorType

from .base import ORMModel



# --- Comment ---


class CommentCreate(BaseModel):
    anchor_type: CommentAnchorType
    anchor_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    body: str = Field(min_length=1)


class CommentRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    anchor_type: CommentAnchorType
    anchor_id: uuid.UUID
    parent_comment_id: uuid.UUID | None
    author_agent_id: uuid.UUID
    author_name: str | None = None
    body: str
    created_at: datetime
    unresolved_mentions: list[str] = Field(default_factory=list)


class CommentTreeNode(CommentRead):
    children: list["CommentTreeNode"] = Field(default_factory=list)
