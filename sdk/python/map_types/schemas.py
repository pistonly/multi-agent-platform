import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from map_types.enums import (
    AgentRole,
    CommentAnchorType,
    ExperimentPhase,
    FeedbackCategory,
    FeedbackStatus,
    ReviewItemKind,
    ReviewItemStatus,
    TopicActionItemStatus,
    TopicDiscussionRound,
    TopicStatus,
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
    open_topics: list["TopicSummaryRead"] = Field(default_factory=list)
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
    author_name: str | None = None
    body: str
    created_at: datetime
    unresolved_mentions: list[str] = Field(default_factory=list)


class CommentTreeNode(CommentRead):
    children: list["CommentTreeNode"] = Field(default_factory=list)


# --- Experiment ---


class ExperimentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    plan: PlanInput
    submit_for_review: bool = False
    topic_id: uuid.UUID | None = None


class ExperimentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    archived: bool | None = None


class ExperimentSummaryRead(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    creator_agent_id: uuid.UUID
    title: str
    description: str | None
    phase: ExperimentPhase
    current_plan_version: int
    topic_id: uuid.UUID | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    # --- execution lock (per-project; CP-3) ---
    lock_holder_experiment_id: uuid.UUID | None = None
    lock_acquired_at: datetime | None = None
    lock_ttl_seconds: int | None = None
    next_attempt_at: datetime | None = None
    lock_skip_count: int = 0
    # Populated for todos / waker fingerprints; defaults to 0 on list endpoints.
    open_unreasonable_count: int = 0


class ExperimentDetailRead(ExperimentSummaryRead):
    current_plan: PlanVersionRead | None = None
    plan_version_count: int = 0
    review_count: int = 0
    log_count: int = 0
    latest_log_summary: str | None = None
    # --- execution lock (per-project; CP-3) ---
    lock_holder_experiment_id: uuid.UUID | None = None
    lock_acquired_at: datetime | None = None
    lock_ttl_seconds: int | None = None
    next_attempt_at: datetime | None = None
    lock_skip_count: int = 0


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


class ExperimentBundleRead(BaseModel):
    """Aggregated experiment page payload (detail + plans + reviews + comment tree + logs)."""

    experiment: ExperimentDetailRead
    plans: list[PlanVersionRead] = Field(default_factory=list)
    reviews: list[ReviewRead] = Field(default_factory=list)
    comments: list[CommentTreeNode] = Field(default_factory=list)
    logs: list[ExperimentLogRead] = Field(default_factory=list)


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


# --- Topic ---


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class TopicAdvanceRound(BaseModel):
    increment_summary: bool = True
    acknowledged_by: list[uuid.UUID] = Field(default_factory=list)
    ack: Literal["accept", "reject", "dismiss"] | None = None


class TopicActionItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    owner_agent_id: uuid.UUID | None = None
    due_at: datetime | None = None
    linked_experiment_id: uuid.UUID | None = None


class TopicResolve(BaseModel):
    decision: str | None = Field(default=None, min_length=1)
    rationale: str | None = None
    rejected_options: str | None = None
    open_questions: str | None = None
    no_decision_reason: str | None = Field(default=None, min_length=1)
    action_items: list[TopicActionItemCreate] = Field(default_factory=list)

    @field_validator("decision", "rationale", "rejected_options", "open_questions", "no_decision_reason", mode="before")
    @classmethod
    def normalize_blank_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        text = value.strip()
        return text or None

    @model_validator(mode="after")
    def validate_resolution_content(self) -> "TopicResolve":
        if not self.decision and not self.no_decision_reason:
            raise ValueError("decision or no_decision_reason is required")
        return self


class TopicActionItemRead(BaseModel):
    id: uuid.UUID
    decision_id: uuid.UUID
    project_id: uuid.UUID
    topic_id: uuid.UUID
    title: str
    description: str | None = None
    owner_agent_id: uuid.UUID | None = None
    owner_name: str | None = None
    status: TopicActionItemStatus
    due_at: datetime | None = None
    linked_experiment_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class TopicDecisionRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    topic_id: uuid.UUID
    topic_title: str | None = None
    author_agent_id: uuid.UUID
    author_name: str | None = None
    decision: str | None = None
    rationale: str | None = None
    rejected_options: str | None = None
    open_questions: str | None = None
    no_decision_reason: str | None = None
    action_items: list[TopicActionItemRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TopicSummaryRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    creator_agent_id: uuid.UUID
    creator_name: str | None = None
    title: str
    description: str | None
    status: TopicStatus
    pinned: bool = False
    discussion_round: TopicDiscussionRound = TopicDiscussionRound.round1
    round_summary_count: int = 0
    comment_count: int = 0
    experiment_count: int = 0
    last_comment_id: uuid.UUID | None = None
    last_comment_author_agent_id: uuid.UUID | None = None
    last_comment_author_name: str | None = None
    last_comment_excerpt: str | None = None
    my_comment_count: int | None = None
    dismissed_at: datetime | None = None
    advance_round_pending_since: datetime | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class TopicCommentCreate(BaseModel):
    body: str = Field(min_length=1)
    parent_id: uuid.UUID | None = None


class TopicCommentRead(ORMModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    author_agent_id: uuid.UUID
    author_name: str | None = None
    parent_comment_id: uuid.UUID | None
    body: str
    created_at: datetime
    unresolved_mentions: list[str] = Field(default_factory=list)


class TopicCommentTreeNode(TopicCommentRead):
    children: list["TopicCommentTreeNode"] = Field(default_factory=list)


class TopicRead(TopicSummaryRead):
    experiments: list[ExperimentSummaryRead] = Field(default_factory=list)
    comments: list[TopicCommentTreeNode] = Field(default_factory=list)
    decision: TopicDecisionRead | None = None


class PendingReplyRead(BaseModel):
    item_id: uuid.UUID
    experiment_id: uuid.UUID
    experiment_title: str
    content: str
    status: ReviewItemStatus
    updated_at: datetime


class PendingTopicReplyTodoRead(BaseModel):
    topic_id: uuid.UUID
    topic_title: str
    comment_id: uuid.UUID
    parent_comment_id: uuid.UUID | None = None
    thread_root_id: uuid.UUID
    author_agent_id: uuid.UUID
    author_name: str | None = None
    excerpt: str
    created_at: datetime


class MentionTodoRead(BaseModel):
    id: uuid.UUID
    mentioned_agent_id: uuid.UUID
    author_agent_id: uuid.UUID
    author_name: str | None = None
    source_type: str
    source_id: uuid.UUID
    project_id: uuid.UUID
    experiment_id: uuid.UUID | None
    topic_id: uuid.UUID | None
    excerpt: str
    created_at: datetime
    dismissed_at: datetime | None = None


class DismissMentionResultRead(BaseModel):
    id: uuid.UUID
    dismissed_at: datetime


class DismissAllMentionsResultRead(BaseModel):
    dismissed: int


class TopicActionItemTodoRead(BaseModel):
    id: uuid.UUID
    decision_id: uuid.UUID
    project_id: uuid.UUID
    topic_id: uuid.UUID
    topic_title: str
    title: str
    description: str | None = None
    status: TopicActionItemStatus
    due_at: datetime | None = None
    linked_experiment_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class TodoRead(BaseModel):
    my_open_experiments: list[ExperimentSummaryRead] = Field(default_factory=list)
    pending_reviews: list[ExperimentSummaryRead] = Field(default_factory=list)
    pending_replies: list[PendingReplyRead] = Field(default_factory=list)
    pending_topic_replies: list[PendingTopicReplyTodoRead] = Field(default_factory=list)
    my_open_topics: list[TopicSummaryRead] = Field(default_factory=list)
    mentions: list[MentionTodoRead] = Field(default_factory=list)
    action_items: list[TopicActionItemTodoRead] = Field(default_factory=list)


# --- Notification ---


class NotificationRead(ORMModel):
    id: uuid.UUID
    recipient_agent_id: uuid.UUID
    project_id: uuid.UUID | None
    event: str
    summary: str
    target_type: str
    target_id: uuid.UUID | None
    payload_json: dict | None
    read_at: datetime | None
    created_at: datetime


class NotificationListRead(BaseModel):
    items: list[NotificationRead]
    total: int
    unread_count: int


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
    payload: dict
    status_code: int | None
    attempts: int
    success: bool
    last_attempt_at: datetime | None
    created_at: datetime


# --- Audit ---


class AuditLogRead(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    project_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    summary: str | None
    payload_json: dict | None
    created_at: datetime


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


ProjectStatusRead.model_rebuild()
CommentTreeNode.model_rebuild()
TopicRead.model_rebuild()
TopicCommentTreeNode.model_rebuild()
