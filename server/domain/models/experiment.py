import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from map_types.enums import (
    CommentAnchorType,
    ExperimentMode,
    ExperimentPhase,
    PhaseOwner,
    ResolutionReason,
    ReviewArchivedReason,
    ReviewItemKind,
    ReviewItemStatus,
    ReviewSubstituteKind,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from server.db.base import Base

if TYPE_CHECKING:
    from server.domain.models.project import Agent, Project





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


class ExperimentTransitionReceipt(Base):
    """实验 lifecycle transition 的持久回执（实验B 24f3e565 B2/B5）。

    - nonce（token 内一次性随机数）为主键：同一 token 重放 commit 命中本行
      即返回原 receipt（``response_snapshot``），不重复写 audit、不重复发
      通知（B2 幂等）。
    - ``base_revision`` 为提交时刻该实验已 committed 的 receipt 计数，充当
      单调乐观锁（B1 七元组之一 / B5 CAS）——**不改 experiments 表**：
      ``init_db`` 的 create_all 不给既有表补列（server/db/session.py），
      receipt 计数即版本。
    - ``fingerprint`` 是 validate 时绑定的 workspace 指纹
      （``st_dev + st_ino``）；commit 时 server 对记录的 workspace_path
      现场 re-stat 复核（B8 双侧门禁）。Web 兼容包装路径为 server 自算
      指纹（server-authoritative）。
    """

    __tablename__ = "experiment_transition_receipts"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_phase: Mapped[str] = mapped_column(String(20), nullable=False)
    to_phase: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
