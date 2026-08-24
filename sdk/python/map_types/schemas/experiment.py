"""Experiment lifecycle / review / lock schemas."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from map_types.enums import AcceptanceType, ExperimentMode, ExperimentPhase, PhaseOwner, ReviewVerdict

from .base import ORMModel
from .comment import CommentTreeNode
from .content_source import ContentSourceMeta
from .plan import PlanInput, PlanVersionRead
from .review import ReviewRead

# --- Experiment ---


class ExperimentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    plan: PlanInput
    submit_for_review: bool = False
    topic_id: uuid.UUID | None = None
    # v0.10: ``direct`` mode skips reviewer gates (draft → running → done).
    # When ``mode=direct``, ``submit_for_review`` is silently ignored
    # (direct mode has no review phase to submit to).
    mode: ExperimentMode = ExperimentMode.standard
    # MAP slimming: when set, the plan MD lives at this local path and
    # ``plan.content_md`` may be a stub. Stored on the Experiment row.
    plan_file_path: str | None = None


class ExperimentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    archived: bool | None = None


class ExperimentStart(BaseModel):
    """Optional body for ``POST /experiments/{id}/start`` (migration 042).

    When omitted (or ``executor_agent_id`` is null) the host self-executes
    and the server populates ``experiments.executor_agent_id`` with the
    caller's id. When set, that agent becomes the sole non-admin caller
    allowed to ``complete`` the experiment.
    """

    executor_agent_id: uuid.UUID | None = None


class ExperimentSummaryRead(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    creator_agent_id: uuid.UUID
    # Migration 042: agent designated to run the experiment (calls
    # ``complete``). NULL on legacy experiments; falls back to creator
    # for permission checks. Set by ``start_experiment``.
    executor_agent_id: uuid.UUID | None = None
    title: str
    description: str | None
    phase: ExperimentPhase
    # v0.10: experiment lifecycle mode.
    mode: ExperimentMode = ExperimentMode.standard
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
    # MAP slimming: local MD file paths for plan and log.
    plan_file_path: str | None = None
    log_file_path: str | None = None
    source: ContentSourceMeta | None = None


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
    # MAP slimming (v0.13 M57): when file_path is set, the log MD lives at
    # this local path and ``content_md`` may be omitted. The server stores a
    # stub (``See file: <path>``) in experiment_logs.content_md and the
    # file_path on the row. Evidence validation is metadata-driven and thus
    # identical for both forms; the content-based similarity check is skipped
    # for the slim form (marked ``similarity_skipped: slim form``).
    content_md: str | None = Field(default=None, min_length=1)
    file_path: str | None = None
    metadata: dict[str, Any] | None = None
    # b72d0542 I1.b(2)(e): when the similarity check would emit a warning,
    # callers can set this to True to acknowledge and skip the soft
    # warning. Server writes a ``log.force_skip`` audit row when the
    # warning was actually suppressed (no-op when no warning fired).
    force_skip_similarity: bool = False

    @model_validator(mode="after")
    def _require_content_or_path(self) -> "ExperimentLogCreate":
        if not self.content_md and not self.file_path:
            raise ValueError("Either content_md or file_path must be provided")
        return self


class ExperimentLogRead(ORMModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    author_agent_id: uuid.UUID
    summary: str
    content_md: str
    file_path: str | None = None
    metadata_json: dict[str, Any] | None
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
    # v0.13 M57 slim form (``file_path`` payload): the content-based
    # similarity check is skipped — ``similarity_skipped`` carries the
    # reason (``"slim form"``) instead of a silent empty warning list, and
    # ``summary_repeat_hint`` fires a non-blocking anti-abuse hint when
    # the summary exactly repeats the prior log's summary. Both are None
    # for the full ``content_md`` form.
    similarity_skipped: str | None = None
    summary_repeat_hint: str | None = None


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
    content_md: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] | None = None
    # MAP slimming: when set, the log MD lives at this local path and
    # ``content_md`` may be omitted. Stored on the Experiment row.
    log_file_path: str | None = None
    # 50cddb7e I4 (A3): scored-test debt refs (issue/债条目) that exempt
    # a ``pytest_summary.failed > 0`` hard gate at complete time. Strings
    # reference a registered debt entry — minimal form, no schema beyond "查
    # 得到即可".
    known_failures: list[str] = Field(
        default_factory=list,
        description="Known-failure refs that waive the failed>0 pytest_summary gate.",
    )

    @model_validator(mode="after")
    def _require_content_or_path(self) -> "ExperimentComplete":
        if not self.content_md and not self.log_file_path:
            raise ValueError("Either content_md or log_file_path must be provided")
        return self


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
    metadata: dict[str, Any] | None = None
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
