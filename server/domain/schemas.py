import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.domain.models import (
    AgentRole,
    CommentAnchorType,
    ExperimentPhase,
    ReviewItemKind,
    ReviewItemStatus,
)

PROJECT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]{0,62}$")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Project ---


class ProjectCreate(BaseModel):
    project_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    workspace_path: str = Field(min_length=1, max_length=1024)
    description: str | None = None

    @field_validator("project_key")
    @classmethod
    def validate_project_key(cls, value: str) -> str:
        key = value.strip().lower()
        if not PROJECT_KEY_PATTERN.match(key):
            raise ValueError("project_key must match [a-z0-9][a-z0-9-_]{0,62}")
        return key


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    workspace_path: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = None
    archived: bool | None = None


class ProjectRead(ORMModel):
    id: uuid.UUID
    project_key: str
    name: str
    workspace_path: str
    description: str | None
    current_status_version: int
    created_at: datetime
    archived_at: datetime | None


class ProjectStatusRead(BaseModel):
    project: ProjectRead
    experiment_counts_by_phase: dict[str, int]
    active_experiments: list["ExperimentSummaryRead"] = Field(default_factory=list)
    recent_experiments: list["ExperimentSummaryRead"]
    status_version: int = 0
    status_md: str | None = None
    status_updated_at: datetime | None = None


class ProjectStatusRevise(BaseModel):
    content_md: str = Field(min_length=1)
    change_note: str | None = Field(default=None, max_length=1024)


class ProjectStatusVersionRead(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    content_md: str
    author_agent_id: uuid.UUID
    change_note: str | None
    created_at: datetime


# --- Plan ---


class PlanInput(BaseModel):
    content_md: str = Field(min_length=1)
    change_note: str | None = Field(default=None, max_length=1024)


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


# --- Review ---


class ReviewCreate(BaseModel):
    reasonable_items: list[str] = Field(default_factory=list)
    unreasonable_items: list[str] = Field(default_factory=list)


class ReviewItemRead(ORMModel):
    id: uuid.UUID
    review_id: uuid.UUID
    kind: ReviewItemKind
    content: str
    status: ReviewItemStatus | None
    created_at: datetime
    updated_at: datetime


class ReviewItemUpdate(BaseModel):
    status: ReviewItemStatus


class ReviewRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    reviewer_agent_id: uuid.UUID
    plan_version: int
    created_at: datetime
    items: list[ReviewItemRead] = Field(default_factory=list)


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
    body: str
    created_at: datetime


class CommentTreeNode(CommentRead):
    children: list["CommentTreeNode"] = Field(default_factory=list)


# --- Experiment ---


class ExperimentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    plan: PlanInput
    submit_for_review: bool = False


class ExperimentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None


class ExperimentSummaryRead(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    creator_agent_id: uuid.UUID
    title: str
    description: str | None
    phase: ExperimentPhase
    current_plan_version: int
    created_at: datetime
    updated_at: datetime


class ExperimentDetailRead(ExperimentSummaryRead):
    current_plan: PlanVersionRead | None = None
    plan_version_count: int = 0
    open_unreasonable_count: int = 0
    review_count: int = 0
    log_count: int = 0
    latest_log_summary: str | None = None


# --- Log ---


class ExperimentLogCreate(BaseModel):
    summary: str = Field(min_length=1, max_length=1024)
    content_md: str = Field(min_length=1)
    metadata: dict | None = None


class ExperimentLogRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    author_agent_id: uuid.UUID
    summary: str
    content_md: str
    metadata_json: dict | None
    created_at: datetime


class ExperimentComplete(BaseModel):
    summary: str = Field(min_length=1, max_length=1024)
    content_md: str = Field(min_length=1)
    metadata: dict | None = None


# --- Status ---


class GlobalStatusRead(BaseModel):
    total_experiments_by_phase: dict[str, int]
    projects: list[ProjectStatusRead]
    recent_experiments: list[ExperimentSummaryRead]


class AgentRead(ORMModel):
    id: uuid.UUID
    name: str
    role: AgentRole
    project_id: uuid.UUID | None
    project_key: str | None = None
    created_at: datetime


class AgentCreateResponse(AgentRead):
    api_token: str


ProjectStatusRead.model_rebuild()
CommentTreeNode.model_rebuild()
