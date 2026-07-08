"""Field rename + alias compatibility (experiment 8d52232d acceptance c).

Locks in:
* New canonical field name (stale_since / visibility) accepts client input.
* Old field name (advance_round_pending_since / partition_visibility) still
  reads into the same logical value (AliasChoices).
* Serialized output keeps the deprecated name readable until N=2 so existing
  web/tests/scripts that read it do not break.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from map_types.schemas import (
    PendingAdvanceRoundTodoRead,
    PendingRoundAckTodoRead,
    SummaryBucket,
    SummaryBucketItem,
    TopicDiscussionRound,
    TopicSummaryRead,
    TopicWorkItemRead,
)

NOW = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)


def test_topic_summary_accepts_new_field_name():
    summary = TopicSummaryRead.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "creator_agent_id": str(uuid.uuid4()),
            "title": "t",
            "description": None,
            "status": "open",
            "stale_since": NOW.isoformat(),
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    assert summary.stale_since == NOW


def test_topic_summary_accepts_old_field_name_as_alias():
    summary = TopicSummaryRead.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "creator_agent_id": str(uuid.uuid4()),
            "title": "t",
            "description": None,
            "status": "open",
            "advance_round_pending_since": NOW.isoformat(),
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    assert summary.stale_since == NOW
    # Serialization still exposes the deprecated alias key so old clients
    # reading the response do not break before N=2.
    dumped = summary.model_dump(mode="json")
    assert dumped["stale_since"] == NOW.isoformat().replace("+00:00", "Z")
    assert dumped["advance_round_pending_since"] == NOW.isoformat().replace("+00:00", "Z")


def test_pending_round_ack_read_accepts_both_names():
    payload = {
        "topic_id": str(uuid.uuid4()),
        "topic_title": "t",
        "discussion_round": TopicDiscussionRound.round1.value,
        "stale_since": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    fresh = PendingRoundAckTodoRead.model_validate(payload)
    assert fresh.stale_since == NOW

    legacy_payload = {**payload}
    legacy_payload.pop("stale_since")
    legacy_payload["advance_round_pending_since"] = NOW.isoformat()
    legacy = PendingRoundAckTodoRead.model_validate(legacy_payload)
    assert legacy.stale_since == NOW


def test_pending_advance_round_read_accepts_both_names():
    payload = {
        "topic_id": str(uuid.uuid4()),
        "topic_title": "t",
        "discussion_round": TopicDiscussionRound.round2.value,
        "stale_since": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    fresh = PendingAdvanceRoundTodoRead.model_validate(payload)
    assert fresh.stale_since == NOW

    legacy_payload = {**payload}
    legacy_payload.pop("stale_since")
    legacy_payload["advance_round_pending_since"] = NOW.isoformat()
    legacy = PendingAdvanceRoundTodoRead.model_validate(legacy_payload)
    assert legacy.stale_since == NOW


def test_topic_work_item_accepts_both_names():
    payload = {
        "kind": "round_ack",
        "priority": "obligation",
        "topic_id": str(uuid.uuid4()),
        "topic_title": "t",
        "required_agent_id": str(uuid.uuid4()),
        "reason": "round_summary_pending",
        "idempotency_key": "k",
        "clear_action": "ack",
        "excerpt": "x",
        "created_at": NOW.isoformat(),
        "stale_since": NOW.isoformat(),
    }
    fresh = TopicWorkItemRead.model_validate(payload)
    assert fresh.stale_since == NOW

    legacy_payload = {**payload}
    legacy_payload.pop("stale_since")
    legacy_payload["advance_round_pending_since"] = NOW.isoformat()
    legacy = TopicWorkItemRead.model_validate(legacy_payload)
    assert legacy.stale_since == NOW


def test_summary_bucket_visibility_alias():
    bucket = SummaryBucket(kind="mention", count=2, visibility="host_only")
    assert bucket.visibility == "host_only"
    dumped = bucket.model_dump(mode="json")
    assert dumped["visibility"] == "host_only"
    assert dumped["partition_visibility"] == "host_only"


def test_summary_bucket_includes_items_shape():
    bucket = SummaryBucket(
        kind="pending_reply",
        count=1,
        visibility="all",
        items=[
            SummaryBucketItem(
                kind="pending_reply",
                topic_id=uuid.uuid4(),
                topic_title="discussion",
                excerpt="reply needed",
                updated_at=NOW,
            )
        ],
        top_excerpt="reply needed",
    )
    assert bucket.count == 1
    assert bucket.items[0].excerpt == "reply needed"
    assert bucket.top_excerpt == "reply needed"
