"""Agent todo partition schemas."""

import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field

from map_types.enums import ExperimentPhase, ReviewItemStatus, TopicActionItemStatus

from .experiment import ExperimentSummaryRead
from .topic import TopicSummaryRead


class PendingReplyRead(BaseModel):
    item_id: uuid.UUID
    experiment_id: uuid.UUID
    experiment_title: str
    content: str
    status: ReviewItemStatus
    updated_at: datetime


class PendingPlanRevisionRead(BaseModel):
    experiment_id: uuid.UUID
    experiment_title: str
    current_plan_version: int
    open_unreasonable_count: int
    blocked_on: str = "open_unreasonable_item"
    actions: list[str] = Field(default_factory=lambda: ["plan_revise"])
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


class PendingRoundAckTodoRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    topic_id: uuid.UUID
    topic_title: str
    discussion_round: str
    round_summary_count: int = 0
    summary_comment_id: uuid.UUID | None = None
    summary_excerpt: str | None = None
    stale_since: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("stale_since", "advance_round_pending_since"),
    )
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def advance_round_pending_since(self) -> datetime | None:
        """DEPRECATED alias for stale_since."""
        return self.stale_since


class PendingAdvanceRoundTodoRead(BaseModel):
    """Host-owned topics where participant acks are complete and advance-round is due."""

    model_config = ConfigDict(populate_by_name=True)

    topic_id: uuid.UUID
    topic_title: str
    discussion_round: str
    round_summary_count: int = 0
    stale_since: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("stale_since", "advance_round_pending_since"),
    )
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def advance_round_pending_since(self) -> datetime | None:
        """DEPRECATED alias for stale_since."""
        return self.stale_since


class StaleOpenTopicTodoRead(BaseModel):
    """Host-owned open topic that has had no activity for the stale threshold."""

    topic_id: uuid.UUID
    topic_title: str
    discussion_round: str
    round_summary_count: int = 0
    stale_since: datetime
    updated_at: datetime


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


class TopicReadCursorRead(BaseModel):
    topic_id: uuid.UUID
    agent_id: uuid.UUID
    last_read_comment_seq: int
    updated_at: datetime


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
    # 同 TopicActionItemRead.linked_experiment_phase：让 todos 消费者（CLI /
    # waker / Web）能区分"等 reviewer 审批"与"host 自身待办"。
    linked_experiment_phase: ExperimentPhase | None = None
    # Wake / stale escalation fields (experiment B). The waker applies
    # should_wake_action_item() directly to this payload — see
    # cli/action_item_escalation.py scan_pending_action_items() for the caller.
    wake_count: int = 0
    first_open_at: datetime | None = None
    last_woken_at: datetime | None = None
    stale_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ExperimentReviewInformationalRead(BaseModel):
    """Read-only experiment review snapshot for non-reviewer personas."""

    experiment_title: str
    phase: ExperimentPhase
    updated_at: datetime
    review_progress: str


class TodoRead(BaseModel):
    my_open_experiments: list[ExperimentSummaryRead] = Field(default_factory=list)
    pending_reviews: list[ExperimentSummaryRead] = Field(default_factory=list)
    pending_result_reviews: list[ExperimentSummaryRead] = Field(default_factory=list)
    experiment_review_informational: list[ExperimentReviewInformationalRead] = Field(
        default_factory=list
    )
    pending_replies: list[PendingReplyRead] = Field(default_factory=list)
    pending_plan_revisions: list[PendingPlanRevisionRead] = Field(default_factory=list)
    pending_topic_replies: list[PendingTopicReplyTodoRead] = Field(default_factory=list)
    pending_round_acks: list[PendingRoundAckTodoRead] = Field(default_factory=list)
    pending_advance_rounds: list[PendingAdvanceRoundTodoRead] = Field(default_factory=list)
    stale_open_topics: list[StaleOpenTopicTodoRead] = Field(default_factory=list)
    my_open_topics: list[TopicSummaryRead] = Field(default_factory=list)
    mentions: list[MentionTodoRead] = Field(default_factory=list)
    action_items: list[TopicActionItemTodoRead] = Field(default_factory=list)
