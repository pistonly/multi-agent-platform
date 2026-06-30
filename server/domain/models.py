import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from map_types.enums import (
    AgentRole,
    CommentAnchorType,
    ExperimentPhase,
    FeedbackCategory,
    FeedbackStatus,
    MentionSourceType,
    ReviewItemKind,
    ReviewItemStatus,
    TopicActionItemStatus,
    TopicDiscussionRound,
    TopicStatus,
)
from server.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_status_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="project")
    status_versions: Mapped[list["ProjectStatusVersion"]] = relationship(
        back_populates="project", order_by="ProjectStatusVersion.version"
    )
    topics: Mapped[list["Topic"]] = relationship(back_populates="project")
    topic_decisions: Mapped[list["TopicDecision"]] = relationship(back_populates="project")


class ProjectStatusVersion(Base):
    __tablename__ = "project_status_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_status_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="status_versions")
    author: Mapped["Agent"] = relationship()


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    api_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token_prefix: Mapped[str] = mapped_column(String(8), nullable=False, default="", index=True)
    role: Mapped[AgentRole] = mapped_column(Enum(AgentRole), default=AgentRole.agent, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project | None"] = relationship()
    created_experiments: Mapped[list["Experiment"]] = relationship(back_populates="creator")
    plan_versions: Mapped[list["PlanVersion"]] = relationship(back_populates="author")
    reviews: Mapped[list["Review"]] = relationship(back_populates="reviewer")
    comments: Mapped[list["Comment"]] = relationship(back_populates="author")
    logs: Mapped[list["ExperimentLog"]] = relationship(back_populates="author")


_ACTIVE_TOPIC_EXPERIMENT_PHASES_SQL = "('draft','review','approved','running')"
_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE = (
    f"topic_id IS NOT NULL AND deleted_at IS NULL AND archived_at IS NULL "
    f"AND phase IN {_ACTIVE_TOPIC_EXPERIMENT_PHASES_SQL}"
)


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index(
            "uq_experiment_one_active_per_topic",
            "topic_id",
            unique=True,
            sqlite_where=text(_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
            postgresql_where=text(_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    creator_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase: Mapped[ExperimentPhase] = mapped_column(
        Enum(ExperimentPhase), default=ExperimentPhase.draft, nullable=False, index=True
    )
    current_plan_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id"), nullable=True, index=True)
    project: Mapped["Project"] = relationship(back_populates="experiments")
    creator: Mapped["Agent"] = relationship(back_populates="created_experiments")
    topic: Mapped["Topic | None"] = relationship(back_populates="experiments")
    plan_versions: Mapped[list["PlanVersion"]] = relationship(
        back_populates="experiment", order_by="PlanVersion.version"
    )
    reviews: Mapped[list["Review"]] = relationship(back_populates="experiment", order_by="Review.created_at")
    comments: Mapped[list["Comment"]] = relationship(back_populates="experiment", order_by="Comment.created_at")
    logs: Mapped[list["ExperimentLog"]] = relationship(back_populates="experiment", order_by="ExperimentLog.created_at")

    # --- execution lock (per-project; CP-3) ---------------------------------
    lock_holder_experiment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    lock_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_skip_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    creator_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TopicStatus] = mapped_column(Enum(TopicStatus), default=TopicStatus.open, nullable=False, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    discussion_round: Mapped[TopicDiscussionRound] = mapped_column(
        Enum(TopicDiscussionRound),
        default=TopicDiscussionRound.round1,
        nullable=False,
        index=True,
    )
    round_summary_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="topics")
    creator: Mapped["Agent"] = relationship()
    comments: Mapped[list["TopicComment"]] = relationship(
        back_populates="topic", order_by="TopicComment.created_at"
    )
    experiments: Mapped[list["Experiment"]] = relationship(back_populates="topic")
    decision: Mapped["TopicDecision | None"] = relationship(back_populates="topic", uselist=False)


class TopicComment(Base):
    __tablename__ = "topic_comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topic_comments.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    topic: Mapped["Topic"] = relationship(back_populates="comments")
    author: Mapped["Agent"] = relationship()
    parent: Mapped["TopicComment | None"] = relationship(remote_side="TopicComment.id")


class TopicDecision(Base):
    __tablename__ = "topic_decisions"
    __table_args__ = (UniqueConstraint("topic_id", name="uq_topic_decision_topic_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_options: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_questions: Mapped[str | None] = mapped_column(Text, nullable=True)
    no_decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="topic_decisions")
    topic: Mapped["Topic"] = relationship(back_populates="decision")
    author: Mapped["Agent"] = relationship()
    action_items: Mapped[list["TopicActionItem"]] = relationship(
        back_populates="decision_record",
        order_by="TopicActionItem.created_at",
        cascade="all, delete-orphan",
    )


class TopicActionItem(Base):
    __tablename__ = "topic_action_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topic_decisions.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    status: Mapped[TopicActionItemStatus] = mapped_column(
        Enum(TopicActionItemStatus), default=TopicActionItemStatus.open, nullable=False, index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    decision_record: Mapped["TopicDecision"] = relationship(back_populates="action_items")
    project: Mapped["Project"] = relationship()
    topic: Mapped["Topic"] = relationship()
    owner: Mapped["Agent | None"] = relationship(foreign_keys=[owner_agent_id])
    linked_experiment: Mapped["Experiment | None"] = relationship(foreign_keys=[linked_experiment_id])


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("experiment_id", "version", name="uq_plan_experiment_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiment: Mapped["Experiment"] = relationship(back_populates="plan_versions")
    author: Mapped["Agent"] = relationship(back_populates="plan_versions")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("experiment_id", "reviewer_agent_id", "plan_version", name="uq_review_per_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False, index=True)
    reviewer_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiment: Mapped["Experiment"] = relationship(back_populates="reviews")
    reviewer: Mapped["Agent"] = relationship(back_populates="reviews")
    items: Mapped[list["ReviewItem"]] = relationship(back_populates="review", order_by="ReviewItem.created_at")


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviews.id"), nullable=False, index=True)
    kind: Mapped[ReviewItemKind] = mapped_column(Enum(ReviewItemKind), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReviewItemStatus | None] = mapped_column(Enum(ReviewItemStatus), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    review: Mapped["Review"] = relationship(back_populates="items")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False, index=True)
    anchor_type: Mapped[CommentAnchorType] = mapped_column(Enum(CommentAnchorType), nullable=False)
    anchor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comments.id"), nullable=True)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiment: Mapped["Experiment"] = relationship(back_populates="comments")
    author: Mapped["Agent"] = relationship(back_populates="comments")
    parent: Mapped["Comment | None"] = relationship(remote_side="Comment.id")


class ExperimentLog(Base):
    __tablename__ = "experiment_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False, index=True)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    summary: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiment: Mapped["Experiment"] = relationship(back_populates="logs")
    author: Mapped["Agent"] = relationship(back_populates="logs")


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    deliveries: Mapped[list["WebhookDelivery"]] = relationship(back_populates="webhook")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    webhook: Mapped["Webhook"] = relationship(back_populates="deliveries")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    summary: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipient_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    recipient: Mapped["Agent"] = relationship()


class Mention(Base):
    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    mentioned_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    source_type: Mapped[MentionSourceType] = mapped_column(Enum(MentionSourceType), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiments.id"), nullable=True)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    excerpt: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    mentioned_agent: Mapped["Agent"] = relationship(foreign_keys=[mentioned_agent_id])
    author: Mapped["Agent"] = relationship(foreign_keys=[author_agent_id])


class PlatformFeedback(Base):
    """Platform-wide feedback inbox.

    A global object (project_id is nullable) so any authenticated agent can
    submit feedback about the MAP platform itself, regardless of which project
    they are bound to. Only admins read/triage the inbox.
    """

    __tablename__ = "platform_feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[FeedbackCategory | None] = mapped_column(Enum(FeedbackCategory), nullable=True)
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(FeedbackStatus), default=FeedbackStatus.new, nullable=False, index=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    author: Mapped["Agent"] = relationship()
    project: Mapped["Project | None"] = relationship()
