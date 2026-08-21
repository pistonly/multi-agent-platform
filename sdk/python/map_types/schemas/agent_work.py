"""Agent work summary schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .agent import AgentRead
from .content_source import ContentSourceMeta
from .notification import NotificationListRead
from .todo import TodoRead
from .topic_progress import TopicProgressListRead


class AgentWorkRead(BaseModel):
    """Unified work snapshot for Web 待办, waker, and CLI ``map work``."""

    agent: AgentRead
    topic_progress: TopicProgressListRead
    todos: TodoRead
    notifications: NotificationListRead
    source: ContentSourceMeta | None = None


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
