import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from map_types.enums import (
    FeedbackCategory,
    FeedbackStatus,
    InboundEventSource,
    MentionSourceType,
    NotificationCategory,
    NotificationFingerprintVersion,
)
from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
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
    from server.domain.models.project import Agent, Project




class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # race experiment (eca0f522) PR2: enforce "at most one notification
        # per (recipient, group_key)" at the DB layer so the upsert race
        # becomes an ``IntegrityError``-or-``ON CONFLICT`` merge instead of
        # producing duplicate rows when concurrent event sources share the
        # same group_key. group_key is nullable — both PG and SQLite allow
        # multiple NULLs in a unique constraint, so the constraint only
        # fires for non-NULL group_keys (matches the existing partial-index
        # convention used by ``uq_experiment_one_active_per_topic``).
        UniqueConstraint("recipient_agent_id", "group_key", name="uq_notifications_recipient_group_key"),
        # T11（2026-08）：list_for_agent 的排序分页热点索引
        # （WHERE recipient_agent_id = ? ORDER BY updated_at DESC）；左前缀
        # 同时覆盖纯 recipient 等值查询。原 5 个低基数/冗余单列索引已在
        # migration 052 裁剪（fingerprint_version / category / read_at /
        # group_key / recipient_agent_id），created_at 保留。
        Index("ix_notifications_recipient_updated", "recipient_agent_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipient_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    category: Mapped[NotificationCategory] = mapped_column(
        Enum(NotificationCategory),
        default=NotificationCategory.digest,
        server_default=NotificationCategory.digest.value,
        nullable=False,
    )
    group_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    wake_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    fingerprint_version: Mapped[NotificationFingerprintVersion] = mapped_column(
        Enum(NotificationFingerprintVersion),
        default=NotificationFingerprintVersion.v2,
        server_default=NotificationFingerprintVersion.v2.value,
        nullable=False,
    )
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    recipient: Mapped["Agent"] = relationship()


class InboundEvent(Base):
    """Waker-side ingest log. Decouples "server published a notification" from
    "waker saw and (about to) act on it" with a stable ``fingerprint`` unique key.

    Audit three-layer split:
      - ``notification`` (event layer, server-side fan-out)
      - ``inbound_event`` (this table, access layer — waker's idempotent ingest)
      - ``runtime-waker-sessions/<id>.jsonl`` (execution layer)

    ``event_id`` matches ``notification.id`` one-to-one (UUID) so reviewers can
    join the three layers without persona-name string joins. ``fingerprint`` is
    the dedup key and is the server-side primary gate for replay rejection
    (Phase 1 ``UNIQUE(fingerprint)`` enforces A1).
    """

    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_inbound_events_fingerprint"),
        Index(
            "ix_inbound_events_agent_source_received",
            "agent_id",
            "source",
            "received_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[InboundEventSource] = mapped_column(
        Enum(InboundEventSource),
        default=InboundEventSource.polling,
        nullable=False,
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    agent: Mapped["Agent"] = relationship()


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
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    author: Mapped["Agent"] = relationship()
    project: Mapped["Project | None"] = relationship()
