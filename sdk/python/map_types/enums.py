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


class ReviewItemKind(str, enum.Enum):
    reasonable = "reasonable"
    unreasonable = "unreasonable"


class ReviewSubstituteKind(str, enum.Enum):
    none = "none"
    admin_for_others = "admin_for_others"
    admin_self_substitute = "admin_self_substitute"


class ReviewItemStatus(str, enum.Enum):
    open = "open"
    addressed = "addressed"
    rebutted = "rebutted"
    resolved = "resolved"
    withdrawn = "withdrawn"
    escalated = "escalated"


class CommentAnchorType(str, enum.Enum):
    plan = "plan"
    review = "review"
    review_item = "review_item"
    comment = "comment"


class TopicStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class TopicDiscussionRound(str, enum.Enum):
    round1 = "round1"
    round2 = "round2"
    ready = "ready"


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
    topic_comment = "topic_comment"


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
