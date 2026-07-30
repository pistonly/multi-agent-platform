import enum


class AgentRole(str, enum.Enum):
    agent = "agent"
    admin = "admin"


class ExperimentPhase(str, enum.Enum):
    draft = "draft"
    review = "review"
    approved = "approved"
    running = "running"
    result_review = "result_review"
    done = "done"
    cancelled = "cancelled"


class PhaseOwner(str, enum.Enum):
    """Decision-owner role for each ``ExperimentPhase`` (experiment f873c287 I1(b)).

    Maps which persona holds the decision authority to *advance* a given
    phase — used by ``informational_only`` auto-classification
    (I1(a): ``actions=[] AND blocked_on AND phase_owner != host``) and by
    the UI "host blocked, waiting on {phase_owner}" copy (I1(d)).

    Semantics — "decision owner", not "executor":
    - ``draft``      → host    (creator drafts the plan)
    - ``review``     → reviewer (non-creator reviews & submits verdict)
    - ``revise``     → host    (revising is a host decision during review)
    - ``approved``   → host    (host decides to start)
    - ``running``    → host    (host owns execution)
    - ``result_review`` → reviewer (non-creator reviews result)
    - ``done``       → host    (host owns archival / follow-ups)
    - ``cancelled``  → host    (host decides to cancel; admin can override)
    """

    host = "host"
    reviewer = "reviewer"
    participant = "participant"
    admin = "admin"


class AcceptanceType(str, enum.Enum):
    migration = "migration"
    smoke = "smoke"
    unit_test = "unit_test"
    integration = "integration"
    manual = "manual"


class ReviewItemKind(str, enum.Enum):
    reasonable = "reasonable"
    unreasonable = "unreasonable"


class ReviewVerdict(str, enum.Enum):
    """Reviewer's per-item verdict on experiment acceptance.

    Drives ``experiment.result_review`` structured verdict files and the
    R6 verdict breakdown in ``experiment_logs.metadata_json``. Parallel
    pattern to ``31793f90`` ``pre_schema_log``.
    """

    passed = "passed"
    failed = "failed"
    waived = "waived"


class ReviewSubstituteKind(str, enum.Enum):
    none = "none"
    admin_for_others = "admin_for_others"
    admin_self_substitute = "admin_self_substitute"


class ReviewArchivedReason(str, enum.Enum):
    """Reason a ``Review`` row was archived.

    ``auto`` — archived automatically by ``plan_revise`` because the host
    bumped ``current_plan_version`` and this row is no longer canonical.
    Also used as the historical backfill marker for rows that predate
    the archive feature (the UI renders those with a
    ``(pre-archive, all reviews shown)`` hint).
    ``manual`` — archived explicitly by an admin or host (e.g. duplicate
    review row, withdrawn reviewer).
    ``superseded`` — the review's plan_version was explicitly superseded
    by a later authoritative review at the same plan_version (rare;
    reserved for the future review-amendment flow).
    """

    auto = "auto"
    manual = "manual"
    superseded = "superseded"


class ReviewItemStatus(str, enum.Enum):
    open = "open"
    addressed = "addressed"
    rebutted = "rebutted"
    resolved = "resolved"
    withdrawn = "withdrawn"
    escalated = "escalated"
    closed = "closed"


class ResolutionReason(str, enum.Enum):
    resolved = "resolved"
    rebutted = "rebutted"
    superseded = "superseded"


class CommentAnchorType(str, enum.Enum):
    plan = "plan"
    review = "review"
    review_item = "review_item"
    comment = "comment"


class TopicStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class TopicDiscussionRound(str, enum.Enum):
    """Known discussion round values.

    The DB column is VARCHAR(20) so arbitrary ``roundN`` strings are accepted.
    ``round1`` and ``round2`` are kept as named constants for readability;
    ``ready`` is the terminal state that gates experiment creation.
    Reaching ``ready`` is a host decision (``advance-round --ready``), not an
    automatic transition after a fixed number of rounds.
    """

    round1 = "round1"
    round2 = "round2"
    ready = "ready"

    @staticmethod
    def parse_round_number(value: str) -> int:
        """Extract the integer N from a ``roundN`` string. Returns 0 for non-matching."""
        if not value or not value.startswith("round"):
            return 0
        try:
            return int(value[len("round"):])
        except ValueError:
            return 0

    @staticmethod
    def next_round(value: str) -> str:
        """Return the next round string: ``round1`` → ``round2`` → ``round3`` → ..."""
        n = TopicDiscussionRound.parse_round_number(value)
        return f"round{n + 1}"

    @staticmethod
    def prev_round(value: str) -> str | None:
        """Return the previous round string, or ``None`` if already at ``round1``.

        ``ready`` is not a ``roundN`` string; callers must handle it separately
        (typically by mapping ``ready`` back to ``round{round_summary_count}``).
        """
        n = TopicDiscussionRound.parse_round_number(value)
        if n <= 1:
            return None
        return f"round{n - 1}"


class TopicActionItemStatus(str, enum.Enum):
    open = "open"
    done = "done"
    cancelled = "cancelled"


class ActionItemCategory(str, enum.Enum):
    """Drives the cancel-reason minimum-length threshold (see ActionItemCancel)."""

    implementation = "implementation"
    decision = "decision"
    unspecified = "unspecified"


class MentionSourceType(str, enum.Enum):
    experiment_comment = "experiment_comment"
    topic = "topic"
    topic_comment = "topic_comment"


class TopicCommentKind(str, enum.Enum):
    user = "user"
    system = "system"


class NotificationCategory(str, enum.Enum):
    wakeable = "wakeable"
    digest = "digest"


class NotificationFingerprintVersion(str, enum.Enum):
    """Marker for which fingerprint dialect a Notification row carries.

    v0.9 (this version) marks every freshly-written notification with ``v2`` so
    the runtime-waker can reject legacy ``v1`` fingerprints (``inbound:<event_id>``)
    at the ingest gate and record ``rejection_count`` instead of resuming. The
    version lives on the Notification row itself — classification does not look
    at it, but the audit / three-layer join does (see PRD §5.1 + topic
    d0df651c Round 1 Summary).
    """

    v1 = "v1"
    v2 = "v2"


class FeedbackCategory(str, enum.Enum):
    bug = "bug"
    suggestion = "suggestion"
    question = "question"
    other = "other"


class FeedbackStatus(str, enum.Enum):
    new = "new"
    triaged = "triaged"
    in_progress = "in_progress"
    resolved = "resolved"


class InboundEventSource(str, enum.Enum):
    """Origin channel through which the runtime-waker received a notification.

    Phase 1 only writes ``polling``. ``sse`` and ``replay`` are reserved for
    Phase 2 (SSE overlay + reconnect compensation) so the enum is forward-compat
    and DB migrations don't need to widen the column later.
    """

    polling = "polling"
    sse = "sse"
    replay = "replay"
