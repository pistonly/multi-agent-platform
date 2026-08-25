import uuid
from datetime import datetime
from typing import Any

from map_types.enums import (
    AgentRole,
    CommentAnchorType,
    ExperimentMode,
    ExperimentPhase,
    FeedbackCategory,
    FeedbackStatus,
    InboundEventSource,
    MentionSourceType,
    NotificationCategory,
    NotificationFingerprintVersion,
    PhaseOwner,
    ResolutionReason,
    ReviewArchivedReason,
    ReviewItemKind,
    ReviewItemStatus,
    ReviewSubstituteKind,
    TopicActionItemStatus,
    TopicCommentKind,
    TopicStatus,
)
from map_types.persona import persona_from_agent_name
from sqlalchemy import (
    JSON,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from server.db.base import Base
from server.domain.encrypted_types import EncryptedString


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_root: Mapped[str] = mapped_column(
        String(64), nullable=False, default="map", server_default="map"
    )
    fs_freshness_sla_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    # D1: waker 心跳可见性 — last_api_seen_at 由任意 /me/work 刷新；last_waker_poll_at
    # 仅 waker 特征请求 (client=waker) 刷新；stale 判定只看后者，降级场景人工 work
    # 不污染归属。null → never（该 agent 无 waker 心跳记录），不进 WARN。
    last_api_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_waker_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project | None"] = relationship()

    # ---- Capability model --------------------------------------------------
    # authz experiment (0e6926fa) PR2: capability strings decouple
    # ``system:*`` gating from the AgentRole enum so future capabilities
    # don't churn the schema. Convention: ``<namespace>:<action>``.
    # ``system:*`` is admin-grade (audit / cross-project). Persona-scoped
    # capabilities (e.g. ``host:scan_stalled``) match the agent's trailing
    # name segment (``multi-agent-platform-host`` / ``<project_key>-host``
    # → ``host:*``) — see the ``persona`` property.
    _ADMIN_CAPABILITY_PREFIX = "system:"
    _PERSONA_CAPABILITIES: dict[str, frozenset[str]] = {
        "host": frozenset(
            {
                "system:scan_stalled",
                "system:audit_export",
                "system:cross_persona_call",
            }
        ),
        "reviewer": frozenset({"review:submit", "review:accept_result"}),
        "participant": frozenset({"topic:comment"}),
    }

    @property
    def persona(self) -> str | None:
        """Infer persona from the agent name's trailing ``-<persona>`` segment.

        Delegates to ``map_types.persona.persona_from_agent_name`` so CLI and
        server share one rule: ``{project_key}-host`` and
        ``multi-agent-platform-host`` both resolve to ``host``.
        """
        return persona_from_agent_name(self.name)

    def has_capability(self, capability: str) -> bool:
        """Return True if this agent can perform ``capability``.

        Order:
        1. ``role == admin`` → any ``system:*`` capability passes.
        2. Persona match → look up the persona's capability set.
        3. Otherwise → False.
        """
        if self.role == AgentRole.admin and capability.startswith(self._ADMIN_CAPABILITY_PREFIX):
            return True
        persona = self.persona
        if persona is not None:
            return capability in self._PERSONA_CAPABILITIES[persona]
        return False
    created_experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="creator", foreign_keys="Experiment.creator_agent_id"
    )
    executed_experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="executor", foreign_keys="Experiment.executor_agent_id"
    )
    escalation_target_experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="escalation_target", foreign_keys="Experiment.escalation_target_agent_id"
    )
    plan_versions: Mapped[list["PlanVersion"]] = relationship(back_populates="author")
    reviews: Mapped[list["Review"]] = relationship(back_populates="reviewer")
    comments: Mapped[list["Comment"]] = relationship(back_populates="author")
    logs: Mapped[list["ExperimentLog"]] = relationship(back_populates="author")


_ACTIVE_TOPIC_EXPERIMENT_PHASES_SQL = "('draft','review','approved','running','result_review')"
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
        # race experiment (eca0f522) PR1: enforce "at most one active
        # holder" at the DB layer so the soft lock acquire race becomes
        # an IntegrityError instead of producing two live holders.
        # Both PG and SQLite get the partial WHERE clause (mirrors the
        # ``uq_experiment_one_active_per_topic`` style above); the
        # alembic migration 036 also adds the PG-side partial index for
        # databases that came up before the model change.
        Index(
            "uq_experiment_lock_holder_active",
            "lock_holder_experiment_id",
            unique=True,
            sqlite_where=text("lock_holder_experiment_id IS NOT NULL"),
            postgresql_where=text("lock_holder_experiment_id IS NOT NULL"),
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
    # v0.10: experiment lifecycle mode. ``standard`` (default) uses the
    # full draft → review → approved → running → result_review → done
    # lifecycle. ``direct`` skips reviewer gates: draft → running → done.
    # Set at creation time; immutable thereafter.
    mode: Mapped[str] = mapped_column(
        String(16), default=ExperimentMode.standard.value, nullable=False
    )
    current_plan_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # M58 (experiment-side three-state routing): ``topic_id`` may reference
    # either a DB ``topics`` row or an FS-plane topic id (uuid5 derived from
    # the ``map/`` folder, no DB row). The ForeignKey is retired — migration
    # 046 drops ``fk_experiments_topic_id``; existence / ownership gates live
    # in ``project_service.create_experiment`` (DB lookup first, FS resolver
    # second). No ORM relationship is kept on this column (verified unused
    # across server/ and tests/ — only MagicMock stubs ever touched it).
    topic_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    project: Mapped["Project"] = relationship(back_populates="experiments")
    creator: Mapped["Agent"] = relationship(back_populates="created_experiments", foreign_keys=[creator_agent_id])
    # --- executor delegation (migration 042) -------------------------------
    # The agent who actually *runs* the experiment (calls ``complete``).
    # Set by ``start_experiment`` when the host delegates execution to
    # another agent (typically a ``participant`` persona). NULL on legacy
    # experiments; ``phase_service.complete_experiment`` falls back to
    # ``creator_agent_id`` in that case. Host-only lifecycle gates
    # (create / approve / start / withdraw / cancel) still check
    # ``creator_agent_id`` — the host retains decision authority.
    executor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, index=True
    )
    executor: Mapped["Agent | None"] = relationship(
        back_populates="executed_experiments", foreign_keys=[executor_agent_id]
    )
    # M58: no ``topic`` relationship — ``topic_id`` may reference FS-plane
    # uuid5 ids with no ``topics`` row (see column comment above).
    # MAP slimming: file paths for plan and log MD files. When present,
    # PlanVersion.content_md / ExperimentLog.content_md store stubs.
    plan_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_versions: Mapped[list["PlanVersion"]] = relationship(
        back_populates="experiment", order_by="PlanVersion.version"
    )
    reviews: Mapped[list["Review"]] = relationship(back_populates="experiment", order_by="Review.created_at")
    comments: Mapped[list["Comment"]] = relationship(back_populates="experiment", order_by="Comment.created_at")
    logs: Mapped[list["ExperimentLog"]] = relationship(back_populates="experiment", order_by="ExperimentLog.created_at")

    # --- phase_owner routing (experiment f873c287 I1(b)) -----------------
    # Decision-owner role for the *current* phase. Drives:
    #   - ``informational_only`` auto-classification (I1(a))
    #   - "host blocked, waiting on {phase_owner}" UI copy (I1(d))
    #   - ``experiments_needing_attention`` persona filter (I1(e))
    # Stored as a string column (16 chars) — kept aligned with the
    # ``PhaseOwner`` enum via ``phase_owner_resolver``. Defaulted by app
    # logic when ``Experiment.phase`` changes (see ``phase_service``).
    phase_owner: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="host", index=True
    )

    @validates("phase_owner")
    def _validate_phase_owner(self, _key: str, value: object) -> str:
        """ORM-side guard: ``phase_owner`` must be a valid ``PhaseOwner``.

        Migration 035 deliberately stores ``phase_owner`` as a plain
        ``String(16)`` (no DB-level CHECK / enum) so the resolver + enum
        remain the single source of truth. The trade-off is that any
        code path that bypasses ``phase_service._sync_phase_owner`` could
        silently write a garbage string. This validator catches such
        drift at the ORM boundary (BEFORE the SQL flush) and raises
        ``ValueError`` so the bad value never reaches the DB.

        Allowed values come from :class:`map_types.enums.PhaseOwner` —
        the same enum the resolver / UI rely on.
        """
        if isinstance(value, PhaseOwner):
            return value.value
        if isinstance(value, str):
            try:
                return PhaseOwner(value).value
            except ValueError:
                pass
        raise ValueError(
            f"Experiment.phase_owner must be a PhaseOwner member "
            f"(host / reviewer / participant / admin); got {value!r}"
        )

    # --- execution lock (per-project; CP-3) ---------------------------------
    lock_holder_experiment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    lock_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_skip_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # --- escalation routing (experiment 156172e9 I1(b)) ---------------------
    # Optional override for STATE_MACHINE.* error escalation. When set, the
    # CLI surfaces this agent as the recovery contact regardless of the
    # caller's role. When NULL, the role-based fallback rule applies:
    # current caller → same-role active agent → admin.
    escalation_target_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True, index=True
    )
    escalation_target: Mapped["Agent | None"] = relationship(
        back_populates="escalation_target_experiments",
        foreign_keys=[escalation_target_agent_id],
    )


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
    substitute_kind: Mapped[ReviewSubstituteKind] = mapped_column(
        Enum(ReviewSubstituteKind),
        nullable=False,
        default=ReviewSubstituteKind.none,
        server_default="none",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Archive metadata (experiment 18f1d8f6 I1(a)). Both columns are NULL
    # for active reviews; ``archived_at`` is set the moment a row becomes
    # archived. ``archived_reason`` is also used as a historical backfill
    # marker for rows that predate the archive feature (migration 034
    # sets ``archived_reason='auto'`` with ``archived_at=NULL`` to keep
    # the rows visible during the N=2 transition).
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_reason: Mapped[ReviewArchivedReason | None] = mapped_column(
        Enum(ReviewArchivedReason),
        nullable=True,
    )

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
    last_resolution_reason: Mapped["ResolutionReason | None"] = mapped_column(
        Enum(ResolutionReason), nullable=True
    )
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
    # v0.13 M57 slim form: relative path to the local log MD file; when
    # present, content_md holds a stub ("See file: <path>").
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiment: Mapped["Experiment"] = relationship(back_populates="logs")
    author: Mapped["Agent"] = relationship(back_populates="logs")


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # c9281d86 PR3: store as Fernet ciphertext at rest. Application code
    # still reads / writes plaintext via the TypeDecorator — no API or
    # service-layer change required. Migration 037 backfills existing
    # plaintext rows in-place.
    secret: Mapped[str] = mapped_column(EncryptedString(1024), nullable=False)
    events: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
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
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    webhook: Mapped["Webhook"] = relationship(back_populates="deliveries")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    summary: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # query_by_target: WHERE target_type=? AND target_id=? ORDER BY created_at DESC
        # 复合索引左前缀覆盖现有 target_id 单列查询，避免重复索引。
        Index("ix_audit_logs_target", "target_type", "target_id"),
        # query_all: ORDER BY created_at DESC LIMIT/OFFSET 分页
        Index("ix_audit_logs_created_at", "created_at"),
    )


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
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipient_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
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
        index=True,
    )
    group_key: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    wake_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    fingerprint_version: Mapped[NotificationFingerprintVersion] = mapped_column(
        Enum(NotificationFingerprintVersion),
        default=NotificationFingerprintVersion.v2,
        server_default=NotificationFingerprintVersion.v2.value,
        nullable=False,
        index=True,
    )
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
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


class FsProjection(Base):
    """FS plane 投影缓存（远程/容器部署的读侧回退源）。

    内容主权仍在本地 ``map/`` 文件夹；本表只存 ``map fs push`` 上行的
    解析快照（FsProjectionPushRequest 的 JSON 形态），每 project 一行、
    幂等覆盖。server 读路径在 workspace 不可达时回退到该快照；commit
    验证型写时顺带把 fields 应用到快照，避免额外一轮 push。
    """

    __tablename__ = "fs_projections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, unique=True, index=True
    )
    pushed_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    publisher_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    client_workspace: Mapped[str] = mapped_column(String(1024), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pushed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FsWriteReceipt(Base):
    """一次性 FS 写凭证的消费记录，阻止 commit token 重放。"""

    __tablename__ = "fs_write_receipts"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
