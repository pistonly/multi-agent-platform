import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from map_types.enums import (
    AcceptanceType,
    ActionItemCategory,
    AgentRole,
    CommentAnchorType,
    ExperimentPhase,
    FeedbackCategory,
    FeedbackStatus,
    InboundEventSource,
    NotificationCategory,
    NotificationFingerprintVersion,
    PhaseOwner,
    ResolutionReason,
    ReviewArchivedReason,
    ReviewItemKind,
    ReviewItemStatus,
    ReviewSubstituteKind,
    ReviewVerdict,
    TopicActionItemStatus,
    TopicCommentKind,
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
    substitute_reason: str | None = Field(
        default=None,
        min_length=1,
        description="Required when admin submits a substitute review (admin_for_others).",
    )


class ReviewItemRead(ORMModel):
    id: uuid.UUID
    review_id: uuid.UUID
    kind: ReviewItemKind
    content: str
    status: ReviewItemStatus | None
    last_resolution_reason: ResolutionReason | None = None
    created_at: datetime
    updated_at: datetime
    waived_reason: str | None = Field(
        default=None,
        description=(
            "Populated server-side from the latest accept-result verdict_file "
            "where verdict='waived' for this item. Read-only convenience field "
            "for participant-facing UIs; the source of truth is "
            "experiment_logs.metadata_json.verdict_file."
        ),
    )


class ReviewItemUpdate(BaseModel):
    status: ReviewItemStatus


class ReviewRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    reviewer_agent_id: uuid.UUID
    plan_version: int
    substitute_kind: ReviewSubstituteKind = ReviewSubstituteKind.none
    created_at: datetime
    archived_at: datetime | None = None
    archived_reason: ReviewArchivedReason | None = None
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
    log_count: int = 0
    latest_log_summary: str | None = None
    # Per-agent capabilities (experiment plan v2 AC#3); populated when actor context exists.
    actions: list[str] = Field(default_factory=list)
    blocked_on: str | None = None
    # Historical annotation: approved/running/done without qualifying non-creator review.
    legacy_self_review: bool = False
    # --- phase routing (experiment f873c287 I1(b)) -----------------------
    # Decision-owner role for the current phase. Drives the UI "host
    # blocked, waiting on {phase_owner}" copy and the
    # ``informational_only`` partition flag below.
    phase_owner: PhaseOwner = PhaseOwner.host
    # Auto-classification (I1(a)): True iff
    #   actions == [] AND blocked_on != None AND phase_owner != host.
    # Surfaces in my_open_experiments so waker / web UI can demote the
    # entry out of the host's actionable obligation bucket. The
    # partition is "informational only" — host can still see and act
    # on it; the UI just shows a "waiting on {phase_owner}" copy.
    informational_only: bool = False
    # 0db51e10 I3(5d): phase visibility whitelist fold. True iff the
    # configured whitelist excludes the current phase for the caller's
    # role. Mirrors ``actions == [] AND blocked_on ==
    # "hidden_for_current_persona"`` so CLI / web UI can render a
    # single "folded" copy without leaking actions.
    hidden_for_current_persona: bool = False
    # b72d0542 I1.b: 4-段 template soft validation result, populated only
    # on ``experiment complete`` calls. None on other endpoints (status,
    # list, etc.) — server sets it explicitly in ``complete_experiment``.
    template_validation: "TemplateValidationSchema | None" = None


class AcceptanceStatusRead(BaseModel):
    id: str
    description: str
    acceptance_type: AcceptanceType
    evidence_provided: bool = False
    reviewer_verdict: str | None = None


class ExperimentDetailRead(ExperimentSummaryRead):
    current_plan: PlanVersionRead | None = None
    plan_version_count: int = 0
    review_count: int = 0
    acceptance_status: list[AcceptanceStatusRead] = Field(default_factory=list)


class ExperimentLockRead(ORMModel):
    """Per-project execution-lock snapshot (CP-3).

    定义在 ``map_types.schemas`` 以便 server API 与 SDK 共享同一响应模型；
    server 端用 ``ExperimentLockRead.model_validate(result)`` 从 lock_service
    的结果对象构造（``ORMModel`` 已开启 ``from_attributes``）。
    """

    experiment_id: uuid.UUID
    project_id: uuid.UUID
    holder: uuid.UUID | None = None
    acquired_at: datetime | None = None
    ttl_seconds: int | None = None
    next_attempt_at: datetime | None = None
    skip_count: int = 0


class ExperimentLockStalledScanRead(BaseModel):
    notification_ids: list[uuid.UUID]
    emitted_count: int


# --- Log ---


class ExperimentLogCreate(BaseModel):
    summary: str = Field(min_length=1, max_length=1024)
    content_md: str = Field(min_length=1)
    metadata: dict | None = None
    # b72d0542 I1.b(2)(e): when the similarity check would emit a warning,
    # callers can set this to True to acknowledge and skip the soft
    # warning. Server writes a ``log.force_skip`` audit row when the
    # warning was actually suppressed (no-op when no warning fired).
    force_skip_similarity: bool = False


class ExperimentLogRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    author_agent_id: uuid.UUID
    summary: str
    content_md: str
    metadata_json: dict | None
    created_at: datetime


# --- 8ac93d4e I1.c — Log create response wrapper with evidence validation ---

EvidenceWarningCode = Literal["MISSING_EVIDENCE_KEY"]


class EvidenceWarningSchema(BaseModel):
    code: EvidenceWarningCode
    missing_key: str
    plan_required: bool = True
    log_provided: bool = False


class EvidenceValidationSchema(BaseModel):
    """Soft validation result for an ``ExperimentLogCreate`` payload.

    The validator never blocks the log save — ``valid`` is always True.
    ``warnings`` lists evidence_keys declared in plan frontmatter that are
    missing from the supplied metadata; ``parse_error`` is set when the
    plan frontmatter existed but its YAML failed to parse.
    """

    warnings: list[EvidenceWarningSchema] = Field(default_factory=list)
    parse_error: str | None = None
    plan_keys: list[str] = Field(default_factory=list)
    valid: bool = True


class LogCreateResponse(BaseModel):
    """Wrapper returned by ``POST /experiments/{id}/logs`` (8ac93d4e I1.c).

    The persisted ``log`` is unchanged from ``ExperimentLogRead`` so v1
    consumers can still parse the response by reaching into ``log``. The
    new ``validation`` field surfaces soft evidence-key warnings without
    breaking v1 schema.

    b72d0542 I1.b(2)(e): ``similarity_warning`` carries the soft
    content-similarity check result (always None on endpoints other than
    ``POST /experiments/{id}/logs``). When the warning fires AND
    ``force_skip_similarity=True`` was supplied, the warning is suppressed
    in this response and a ``log.force_skip`` audit row is written
    instead. ``force_skip`` field echoes whether the caller opted in
    (False on responses without a similarity check).
    """

    log: ExperimentLogRead
    validation: EvidenceValidationSchema
    similarity_warning: "SimilarityWarningSchema | None" = None
    force_skip: bool = False


# --- b72d0542 I1.b — Result submission 4-段 template validation ---

TemplateWarningCode = Literal[
    "MISSING_TEMPLATE_SECTION",
    "NO_LINK_IN_LOG_SECTION",
    "MALFORMED_MARKDOWN_LINK",
]


class TemplateWarningSchema(BaseModel):
    code: TemplateWarningCode
    section: str | None = None
    detail: str | None = None


class TemplateValidationSchema(BaseModel):
    """Soft validation result for a result submission (b72d0542 I1.b).

    Mirrors :class:`EvidenceValidationSchema` (8ac93d4e I1.c): ``valid``
    is always True (soft validation never blocks ``experiment complete``).
    ``warnings`` lists missing 4-段 sections or malformed markdown links
    detected in the ``## 实施 log`` section body.
    """

    warnings: list[TemplateWarningSchema] = Field(default_factory=list)
    sections_present: list[str] = Field(default_factory=list)
    log_link_count: int = 0
    valid: bool = True


# --- b72d0542 I1.b(2)(e) — Log similarity soft warning ------------------

SimilarityWarningCode = Literal["HIGH_CONTENT_SIMILARITY"]


class SimilarityWarningSchema(BaseModel):
    """Soft warning fired when the new log body is too similar to a
    previous log on the same experiment (b72d0542 I1.b(2)(b)).

    ``score`` is the cosine similarity in ``[0.0, 1.0]`` between the new
    log's content embedding and the most-similar previous log on the
    same experiment. ``threshold`` is the warn threshold (plan default
    0.7). ``ref_log_id`` points to the previous log the score was
    computed against. ``model`` is the embedding model id used (e.g.
    ``sentence-transformers/all-MiniLM-L6-v2``).

    The warning is non-blocking; callers may pass
    ``force_skip_similarity=True`` to suppress it AND write a
    ``log.force_skip`` audit row instead.
    """

    code: SimilarityWarningCode
    score: float
    threshold: float
    ref_log_id: uuid.UUID
    model: str


class ExperimentComplete(BaseModel):
    summary: str = Field(min_length=1, max_length=1024)
    content_md: str = Field(min_length=1)
    metadata: dict | None = None


# --- Review verdict file (accept-result structured) -----------------------


# WaivedReason v2: min_length=50 (Round 2 Summary consensus) +
# max_length=1000 (v2 expanded to fit waiver four-part rationale:
# "豁免什么 / 为何豁免 / 影响哪些下游消费方 / 是否有补偿措施").
WaivedReason = Annotated[
    str,
    Field(
        min_length=50,
        max_length=1000,
        description="Waiver rationale (review item verdict == waived).",
    ),
]


class ReviewVerdictItem(BaseModel):
    item_id: uuid.UUID
    verdict: ReviewVerdict
    reason: WaivedReason | None = Field(
        default=None,
        description="Required when verdict == waived; otherwise optional.",
    )

    @field_validator("verdict", mode="before")
    @classmethod
    def _accept_review_verdict_aliases(cls, v: object) -> object:
        """cli-ux PR3: accept accept|reject|dismiss as aliases.

        Maps to the canonical ``ReviewVerdict`` enum:

        * ``accept`` / ``passed`` → ``ReviewVerdict.passed``
        * ``reject`` / ``failed`` → ``ReviewVerdict.failed``
        * ``dismiss`` / ``waived`` → ``ReviewVerdict.waived``

        Serialized output is still the canonical ``passed|failed|waived``
        string, so DB rows and downstream JSON consumers see no change.
        """
        if isinstance(v, str):
            normalized = v.strip().lower()
            mapping: dict[str, ReviewVerdict] = {
                "accept": ReviewVerdict.passed,
                "passed": ReviewVerdict.passed,
                "reject": ReviewVerdict.failed,
                "failed": ReviewVerdict.failed,
                "dismiss": ReviewVerdict.waived,
                "waived": ReviewVerdict.waived,
            }
            if normalized in mapping:
                return mapping[normalized]
        return v

    @model_validator(mode="after")
    def _waived_requires_reason(self) -> "ReviewVerdictItem":
        if self.verdict == ReviewVerdict.waived and not (self.reason and self.reason.strip()):
            raise ValueError("verdict 'waived' requires non-empty reason (WaivedReason, 50-1000 chars)")
        return self


class ReviewInvariantCheck(BaseModel):
    item_id: uuid.UUID
    verified: bool
    note: str | None = Field(default=None, max_length=1000)


class ReviewVerdictFile(BaseModel):
    review_id: uuid.UUID
    verdicts: list[ReviewVerdictItem] = Field(default_factory=list)
    invariants: list[ReviewInvariantCheck] = Field(default_factory=list)

    @field_validator("verdicts")
    @classmethod
    def _no_duplicate_item_ids(cls, v: list[ReviewVerdictItem]) -> list[ReviewVerdictItem]:
        seen: set[uuid.UUID] = set()
        for item in v:
            if item.item_id in seen:
                raise ValueError(f"Duplicate item_id in verdicts: {item.item_id}")
            seen.add(item.item_id)
        return v


# --- Result decision -------------------------------------------------------


class ExperimentResultDecision(BaseModel):
    summary: str = Field(min_length=1, max_length=1024)
    content_md: str = Field(min_length=1)
    metadata: dict | None = None
    verdict_file: ReviewVerdictFile | None = Field(
        default=None,
        description=(
            "Optional structured verdict file (CLI: --review-verdict-file). "
            "When provided, server validates item_id.review_id ownership and "
            "records pre_schema_accept_result='false' + verdict breakdown in "
            "log metadata. Omit (legacy) → pre_schema_accept_result='true' with "
            "an info-level warning."
        ),
    )


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


class AgentCreate(BaseModel):
    """Body for ``POST /api/v1/agents`` (cleanup experiment f12a5638 P2 #5).

    Replaces the legacy query-param creation contract so the endpoint
    follows the same body-driven pattern as every other write endpoint
    in the API. ``project_id`` and ``project_key`` are both accepted for
    caller convenience — at most one must resolve to a project for
    ``role=agent`` (admin-only). When both are omitted the service layer
    raises ``ValueError`` which maps to 400.
    """

    name: str = Field(min_length=1, max_length=128)
    role: AgentRole = AgentRole.agent
    project_id: uuid.UUID | None = None
    project_key: str | None = None


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
    mark_ready: bool = False


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def advance_round_pending_since(self) -> datetime | None:
        """DEPRECATED alias for stale_since — kept readable for clients still using the old name."""
        return self.stale_since


class TopicCommentCreate(BaseModel):
    body: str = Field(min_length=1)
    parent_id: uuid.UUID | None = None
    is_round_summary: bool = False


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


class TopicCommentTreeNode(TopicCommentRead):
    children: list["TopicCommentTreeNode"] = Field(default_factory=list)


class TopicRead(TopicSummaryRead):
    experiments: list[ExperimentSummaryRead] = Field(default_factory=list)
    comments: list[TopicCommentTreeNode] = Field(default_factory=list)
    decision: TopicDecisionRead | None = None


class TopicProgressCommentRead(ORMModel):
    id: uuid.UUID
    author_agent_id: uuid.UUID
    author_name: str | None = None
    parent_comment_id: uuid.UUID | None
    body: str
    excerpt: str
    created_at: datetime


class TopicWorkItemRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    priority: str
    topic_id: uuid.UUID
    topic_title: str
    source_comment_id: uuid.UUID | None = None
    thread_root_id: uuid.UUID | None = None
    required_agent_id: uuid.UUID
    reason: str
    idempotency_key: str
    clear_action: str
    excerpt: str
    created_at: datetime
    discussion_round: str | None = None
    stale_since: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("stale_since", "advance_round_pending_since"),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def advance_round_pending_since(self) -> datetime | None:
        """DEPRECATED alias for stale_since."""
        return self.stale_since


class TopicProgressItemRead(BaseModel):
    topic_id: uuid.UUID
    topic_title: str
    discussion_round: str
    last_comment_author_agent_id: uuid.UUID | None = None
    last_comment_author_name: str | None = None
    my_last_comment_id: uuid.UUID | None = None
    new_comments: list[TopicProgressCommentRead] = Field(default_factory=list)
    new_comment_count: int = 0
    work_items: list[TopicWorkItemRead] = Field(default_factory=list)


class TopicProgressListRead(BaseModel):
    items: list[TopicProgressItemRead] = Field(default_factory=list)
    total: int = 0


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
    category: NotificationCategory = NotificationCategory.digest
    group_key: str | None = None
    wake_version: int = 1
    fingerprint_version: NotificationFingerprintVersion = NotificationFingerprintVersion.v2
    event_count: int = 1
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    read_at: datetime | None
    created_at: datetime
    updated_at: datetime | None = None


class NotificationListRead(BaseModel):
    items: list[NotificationRead]
    total: int
    unread_count: int


class AgentWorkRead(BaseModel):
    """Unified work snapshot for Web 待办, waker, and CLI ``map work``."""

    agent: AgentRead
    topic_progress: TopicProgressListRead
    todos: TodoRead
    notifications: NotificationListRead


BucketVisibility = Literal["all", "host_only", "reviewer_only", "participant_only"]
SummaryBucketKind = Literal[
    "mention",
    "round_ack",
    "pending_reply",
    "explicit_only",
    "informational_only",
    "action_items",
]


class SummaryBucketItem(BaseModel):
    """A short summary of one item inside a bucket. The full item is in
    ``map work``'s ``todos`` and ``topic_progress``; this is the slice
    rendered on the /work summary card.
    """

    kind: SummaryBucketKind
    topic_id: uuid.UUID | None = None
    topic_title: str | None = None
    excerpt: str | None = None
    updated_at: datetime | None = None


class SummaryBucket(BaseModel):
    """One of the 6 summary buckets exposed by ``map work --summary``.

    A bucket is a kind-based aggregation of action items / context items so the
    UI can render a top-of-page card without scanning the full partition
    list.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: SummaryBucketKind
    count: int
    visibility: BucketVisibility = "all"
    items: list[SummaryBucketItem] = Field(default_factory=list)
    top_excerpt: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def partition_visibility(self) -> BucketVisibility:
        """DEPRECATED alias for visibility — kept readable for clients still using the old name."""
        return self.visibility


class AgentWorkSummaryRead(BaseModel):
    """6-bucket by_kind summary returned by ``map work --summary``.

    Compact view intended for waker quick-scans and the Web /work top card.
    Full per-partition detail stays in ``AgentWorkRead``.
    """

    agent: AgentRead
    buckets: list[SummaryBucket] = Field(default_factory=list)
    topics_needing_attention: int = 0
    experiments_needing_attention: int = 0
    # f873c287 I1(e): per-phase_owner breakdown of the experiment attention
    # counter so host can see "3 are mine, 2 are waiting on reviewer" at a
    # glance. Keys are ``PhaseOwner.value`` strings ("host" / "reviewer" /
    # "participant" / "admin"). Visibility-filtered: under
    # ``visibility_filter_applied=True`` the ``host`` key is dropped because
    # the underlying bucket is host_only.
    experiments_needing_attention_by_owner: dict[str, int] = Field(
        default_factory=dict
    )
    topics_truncated: int = 0
    experiments_truncated: int = 0
    visibility_filter_applied: bool = True
    topics_limit: int = 10
    experiments_limit: int = 5


# --- InboundEvent ---


class InboundEventCreate(BaseModel):
    """Waker-side record of a notification it intends to act on.

    ``fingerprint`` is the dedup key — server enforces ``UNIQUE(fingerprint)``
    and returns 409 Conflict on replay. ``event_id`` should match the upstream
    ``notification.id`` so the three audit layers stay joinable.
    """

    event_id: uuid.UUID
    event_type: str = Field(min_length=1, max_length=64)
    source: InboundEventSource = InboundEventSource.polling
    fingerprint: str = Field(min_length=1, max_length=128)
    payload: dict | None = None


class InboundEventRead(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    source: InboundEventSource
    fingerprint: str
    payload: dict | None
    received_at: datetime
    acked_at: datetime | None
    rejection_count: int = 0


class InboundEventRecordResult(BaseModel):
    """Return shape for ``POST /me/inbound-events``.

    ``status="recorded"`` → first time, waker may proceed.
    ``status="duplicate"`` → fingerprint already existed; treat as already woken
    (Phase 1 server gate; see plan D6).
    ``status="rejected_v1"`` → legacy v1 fingerprint (``inbound:<event_id>``);
    inbound_event row is persisted (or upserted) with ``rejection_count`` bumped
    so the audit table still records the sighting, but the waker MUST NOT
    resume — see plan I2 / M30A acceptance #4.
    """

    status: Literal["recorded", "duplicate", "rejected_v1"]
    event: InboundEventRead


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
    last_error: str | None
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


# 0db51e10 I2(5e): request body for ``POST /experiments/{id}/cross-persona-call``.
# Persisted in audit_logs.payload_json alongside caller_agent_id /
# target_experiment_id / timestamp (created_at column).
class CrossPersonaCallRecord(BaseModel):
    visibility_diff: dict[str, Any] = Field(default_factory=dict)
    result_partition_count: int = Field(default=0, ge=0)
    diff_size: int = Field(default=0, ge=0)


# --- Action Item audit payloads (B I2: waker escalation 三段式) ---


ACTION_ITEM_WAKE_SENT = "action_item.wake_sent"
ACTION_ITEM_STALE = "action_item.stale"


class ActionItemWakeSentPayload(BaseModel):
    """Payload of ``action_item.wake_sent`` audit event.

    Fired by ``runtime-waker.scan_pending_action_items`` whenever ``should_wake_action_item``
    decides to wake the assignee (T+24h / T+72h / every 7d up to 4 times). Pairs with the
    ``action_item.stale`` event but is a distinct, lower-severity audit signal.
    """

    action_item_id: uuid.UUID
    owner_agent_id: uuid.UUID
    topic_id: uuid.UUID
    decision_id: uuid.UUID | None = None
    linked_experiment_id: uuid.UUID | None = None
    wake_count: int
    last_woken_at: datetime
    first_open_at: datetime
    elapsed_since_first_open_seconds: int
    triggered_by: str = "waker.scan_pending_action_items"


class ActionItemStalePayload(BaseModel):
    """Payload of ``action_item.stale`` audit event.

    Fired after the 4th unanswered wake at the next 7d boundary. Pairs with admin
    notification + creator audit-only mark; the runtime-waker stops waking the
    assignee once ``stale_at`` is set. The event itself is independent of any
    state transition (unlike ``action_item.completed`` / ``action_item.cancelled``):
    it is a diagnostic signal that the open item has not progressed.
    """

    action_item_id: uuid.UUID
    owner_agent_id: uuid.UUID
    topic_id: uuid.UUID
    decision_id: uuid.UUID | None = None
    linked_experiment_id: uuid.UUID | None = None
    last_woken_at: datetime | None
    wake_count: int
    stale_after_attempt: int
    admin_notified: bool
    creator_audit_only: bool


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


# --- STATE_MACHINE.* escalation contact (experiment 156172e9 I1(c)) ---


class EscalationTargetRead(ORMModel):
    """Resolution of a STATE_MACHINE.* error's escalation contact.

    Returned by ``GET /api/v1/agents/me/escalation-target?experiment_id=...``.
    The CLI uses this on ``MAPHTTPError`` with a STATE_MACHINE.* error_code
    so the user knows who to ping about a state-machine refusal.
    """

    experiment_id: uuid.UUID | None
    escalation_target_id: uuid.UUID | None
    escalation_label: str  # "@name" or "<no escalation contact>" placeholder
    tier: str  # "experiment_override" | "current_caller" | "same_role_active" | "admin" | "none"


ProjectStatusRead.model_rebuild()
CommentTreeNode.model_rebuild()
TopicRead.model_rebuild()
TopicCommentTreeNode.model_rebuild()
