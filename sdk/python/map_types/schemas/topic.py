"""Topic / discussion round / action item schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from map_types.enums import ActionItemCategory, ExperimentPhase, TopicActionItemStatus, TopicCommentKind, TopicStatus

from .base import ORMModel
from .experiment import ExperimentSummaryRead

# --- Topic ---


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    slug: str | None = Field(default=None, max_length=256)


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class TopicAdvanceRound(BaseModel):
    increment_summary: bool = True
    acknowledged_by: list[uuid.UUID] = Field(default_factory=list)
    ack: Literal["accept", "reject", "dismiss"] | None = None
    mark_ready: bool = False
    waive_ack: bool = False
    waive_reason: str | None = Field(default=None, max_length=1024)


class TopicCloseRequest(BaseModel):
    """Optional body for ``POST /topics/{id}/close`` — records *why* the topic
    is being closed so the team can distinguish "discussed, no experiment
    needed" from a plain closure."""
    close_reason: str | None = Field(default=None, max_length=256)
    close_note: str | None = None


class TopicActionItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    owner_agent_id: uuid.UUID | None = None
    due_at: datetime | None = None
    linked_experiment_id: uuid.UUID | None = None
    category: ActionItemCategory | None = None
    # Optional id enables resolve-time upsert. Service layer preserves status when id
    # matches an existing item; new ids are inserted as open. None means "always insert".
    id: uuid.UUID | None = None


# Minimum reason length enforced per-category; mirrors server-side validation so the
# CLI and API stay consistent (A1 acceptance A1-3 + A1-4).
_CANCEL_REASON_MIN_LENGTH: dict[ActionItemCategory | None, int] = {
    None: 8,
    ActionItemCategory.unspecified: 8,
    ActionItemCategory.implementation: 8,
    ActionItemCategory.decision: 16,
}


def _cancel_reason_min_length(category: ActionItemCategory | None) -> int:
    return _CANCEL_REASON_MIN_LENGTH.get(category, 8)


class ActionItemCancel(BaseModel):
    """Body of ``POST /api/v1/action-items/{id}/cancel`` and ``map action cancel``."""

    reason: str = Field(min_length=1)
    category: ActionItemCategory | None = None

    @model_validator(mode="after")
    def validate_reason_length(self) -> "ActionItemCancel":
        threshold = _cancel_reason_min_length(self.category)
        if len(self.reason.strip()) < threshold:
            raise ValueError(
                f"reason too short: {len(self.reason.strip())} < {threshold} "
                f"(category={self.category.value if self.category else 'unspecified'})"
            )
        return self


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
    # 当前关联实验所处阶段。当 action_item 仍为 open 但实验已进入
    # result_review 时，用户可据此判断"在等 reviewer 审批"而非 host 待办。
    linked_experiment_phase: ExperimentPhase | None = None
    category: ActionItemCategory | None = None
    cancel_reason: str | None = None
    suggested_linked_experiment_id: uuid.UUID | None = None
    suggested_linked_experiment_title: str | None = None
    # Wake / stale escalation fields (experiment B, plan §2 §3). Surfaced so
    # the runtime-waker can run should_wake_action_item() against the same
    # payload it gets from the todos endpoint, avoiding a second fetch.
    wake_count: int = 0
    first_open_at: datetime | None = None
    last_woken_at: datetime | None = None
    stale_at: datetime | None = None
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
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    project_id: uuid.UUID
    creator_agent_id: uuid.UUID
    creator_name: str | None = None
    title: str
    description: str | None
    slug: str | None = None
    status: TopicStatus
    pinned: bool = False
    discussion_round: str = "round1"
    round_summary_count: int = 0
    comment_count: int = 0
    experiment_count: int = 0
    last_comment_id: uuid.UUID | None = None
    last_comment_author_agent_id: uuid.UUID | None = None
    last_comment_author_name: str | None = None
    last_comment_excerpt: str | None = None
    my_comment_count: int | None = None
    dismissed_at: datetime | None = None
    stale_since: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("stale_since", "advance_round_pending_since"),
        description="When this topic's pending action started waiting (round ack, reply, etc.). Renamed from advance_round_pending_since in N=2; old name remains readable until N=2.",
    )
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    close_reason: str | None = None
    close_note: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def advance_round_pending_since(self) -> datetime | None:
        """DEPRECATED alias for stale_since — kept readable for clients still using the old name."""
        return self.stale_since


class TopicCommentCreate(BaseModel):
    body: str | None = Field(default=None, min_length=1)
    parent_id: uuid.UUID | None = None
    is_round_summary: bool = False
    file_path: str | None = None
    excerpt: str | None = Field(default=None, max_length=200)


class TopicCommentRead(ORMModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    author_agent_id: uuid.UUID
    author_name: str | None = None
    parent_comment_id: uuid.UUID | None
    body: str
    kind: TopicCommentKind = TopicCommentKind.user
    is_round_summary: bool = False
    comment_seq: int
    created_at: datetime
    unresolved_mentions: list[str] = Field(default_factory=list)
    file_path: str | None = None
    excerpt: str | None = None


class TopicCommentTreeNode(TopicCommentRead):
    children: list["TopicCommentTreeNode"] = Field(default_factory=list)


class TopicRead(TopicSummaryRead):
    experiments: list[ExperimentSummaryRead] = Field(default_factory=list)
    comments: list[TopicCommentTreeNode] = Field(default_factory=list)
    decision: TopicDecisionRead | None = None
