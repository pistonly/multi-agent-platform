import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from map_types.enums import (
    TopicActionItemStatus,
    TopicCommentKind,
    TopicStatus,
)
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import Base

if TYPE_CHECKING:
    from server.domain.models.experiment import Experiment
    from server.domain.models.project import Agent, Project




class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    creator_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TopicStatus] = mapped_column(Enum(TopicStatus), default=TopicStatus.open, nullable=False, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # MAP slimming: human-readable identifier for file path convention
    # (e.g. docs/topics/<slug>/round1-host.md). Nullable for backward
    # compat; UNIQUE on non-NULL values enforced by partial index.
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    discussion_round: Mapped[str] = mapped_column(
        String(20),
        default="round1",
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
    creator: Mapped["Agent"] = relationship(foreign_keys=[creator_agent_id])
    comments: Mapped[list["TopicComment"]] = relationship(
        back_populates="topic", order_by="TopicComment.created_at"
    )
    # M58: no ``experiments`` relationship — Experiment.topic_id may hold
    # FS-plane uuid5 ids with no row here (see Experiment.topic_id comment).
    decision: Mapped["TopicDecision | None"] = relationship(back_populates="topic", uselist=False)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    advance_round_pending_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    close_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    close_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TopicComment(Base):
    __tablename__ = "topic_comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topic_comments.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # MAP slimming: local MD file path + short excerpt for list views.
    # When file_path is set, body stores a stub (e.g. "See file: ...").
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[TopicCommentKind] = mapped_column(
        Enum(TopicCommentKind), default=TopicCommentKind.user, nullable=False
    )
    is_round_summary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    comment_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    topic: Mapped["Topic"] = relationship(back_populates="comments")
    author: Mapped["Agent"] = relationship()
    parent: Mapped["TopicComment | None"] = relationship(remote_side="TopicComment.id")


class TopicReadCursor(Base):
    __tablename__ = "topic_read_cursors"
    __table_args__ = (UniqueConstraint("topic_id", "agent_id", name="uq_topic_read_cursor_topic_agent"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    last_read_comment_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    wake_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_woken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    decision_record: Mapped["TopicDecision"] = relationship(back_populates="action_items")
    project: Mapped["Project"] = relationship()
    topic: Mapped["Topic"] = relationship()
    owner: Mapped["Agent | None"] = relationship(foreign_keys=[owner_agent_id])
    linked_experiment: Mapped["Experiment | None"] = relationship(foreign_keys=[linked_experiment_id])
