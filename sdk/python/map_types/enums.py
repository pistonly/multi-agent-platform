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


class MentionSourceType(str, enum.Enum):
    experiment_comment = "experiment_comment"
    topic_comment = "topic_comment"


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
