"""Experiment B / I2: action_item.stale audit-event helper.

The helper is the audit-layer primitive that the runtime-waker will eventually
call when the assignee has not responded across the three-stage escalation
window (T+24h → T+72h → every 7d up to 4 times → stale). This file verifies:

- The audit row is written with ``action="action_item.stale"`` and the
  payload contract from ``ActionItemStalePayload`` (B-11 grep-friendly).
- The required invariants reject bad inputs (missing assignee, no prior wake,
  non-positive attempt counter).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from map_types.enums import AgentRole, TopicActionItemStatus, TopicDiscussionRound, TopicStatus
from sqlalchemy import select

from server.domain.models import Agent, AuditLog, Project, Topic, TopicActionItem, TopicDecision
from server.services.audit_service import ACTION_ITEM_STALE, log_action_item_stale

# Sentinel for "argument not supplied" so we can distinguish a defaulted field
# from an explicitly-None argument (Python defaults can't do that on their own).
_UNSET: object = object()


def _seed_item(
    db_session,
    *,
    owner_agent_id=_UNSET,
    wake_count: int = 4,
    last_woken_at=_UNSET,
    first_open_at=_UNSET,
) -> TopicActionItem:
    """Build a TopicActionItem with the FK chain (Project/Agent/Topic/Decision).

    The optional args use a sentinel default so tests can pass an explicit
    ``None`` and exercise the rejection branch (Python's normal default-
    argument semantics collapse None and "unspecified" together).

    By default an ``assignee`` Agent is created so the item has a real owner;
    pass ``owner_agent_id=None`` to exercise the unassigned-item rejection.
    ``last_woken_at`` defaults to "1h ago" so the stale helper accepts it;
    pass ``last_woken_at=None`` to exercise the no-prior-wake rejection.
    """
    if last_woken_at is _UNSET:
        last_woken_at = datetime.now(timezone.utc) - timedelta(hours=1)
    if first_open_at is _UNSET:
        first_open_at = datetime.now(timezone.utc) - timedelta(days=30)

    project = Project(
        project_key=f"p-{uuid.uuid4().hex[:8]}",
        name="B I2 stale test",
        workspace_path="/tmp/b-i2",
    )
    db_session.add(project)
    db_session.flush()

    creator = Agent(
        name=f"creator-{uuid.uuid4().hex[:8]}",
        api_token_hash="x" * 64,
        role=AgentRole.agent,
        project_id=project.id,
    )
    db_session.add(creator)
    db_session.flush()

    if owner_agent_id is _UNSET:
        # Default: create an assignee Agent so the item has a real owner.
        assignee = Agent(
            name=f"assignee-{uuid.uuid4().hex[:8]}",
            api_token_hash="x" * 64,
            role=AgentRole.agent,
            project_id=project.id,
        )
        db_session.add(assignee)
        db_session.flush()
        owner_agent_id = assignee.id
    # else: use whatever was passed (UUID or explicit None)

    topic = Topic(
        project_id=project.id,
        creator_agent_id=creator.id,
        title="I2 stale test topic",
        status=TopicStatus.closed,
        discussion_round=TopicDiscussionRound.ready,
    )
    db_session.add(topic)
    db_session.flush()

    decision = TopicDecision(
        project_id=project.id,
        topic_id=topic.id,
        author_agent_id=creator.id,
        decision="d",
    )
    db_session.add(decision)
    db_session.flush()

    item = TopicActionItem(
        decision_id=decision.id,
        project_id=project.id,
        topic_id=topic.id,
        title="I2 stale test action",
        owner_agent_id=owner_agent_id,
        status=TopicActionItemStatus.open,
        wake_count=wake_count,
        last_woken_at=last_woken_at,
        first_open_at=first_open_at,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def test_log_action_item_stale_writes_audit_row_with_full_payload(db_session):
    item = _seed_item(db_session, wake_count=4)

    entry = log_action_item_stale(
        db_session,
        item=item,
        stale_after_attempt=4,
        admin_notified=True,
        creator_audit_only=True,
    )

    assert entry.action == ACTION_ITEM_STALE == "action_item.stale"
    assert entry.target_type == "topic_action_item"
    assert entry.target_id == item.id
    assert entry.project_id == item.project_id
    assert entry.agent_id == item.owner_agent_id
    assert "stale" in (entry.summary or "")

    payload = entry.payload_json or {}
    assert payload["action_item_id"] == str(item.id)
    assert payload["owner_agent_id"] == str(item.owner_agent_id)
    assert payload["topic_id"] == str(item.topic_id)
    assert payload["decision_id"] == str(item.decision_id)
    assert payload["linked_experiment_id"] is None
    assert payload["wake_count"] == 4
    assert payload["stale_after_attempt"] == 4
    assert payload["admin_notified"] is True
    assert payload["creator_audit_only"] is True
    # last_woken_at is serialized as ISO8601 string by ``mode="json"``
    assert isinstance(payload["last_woken_at"], str)
    parsed = datetime.fromisoformat(payload["last_woken_at"])
    assert parsed == item.last_woken_at


def test_log_action_item_stale_persists_a_row_findable_by_grep(db_session):
    """B-11 acceptance: ``grep '"action": "action_item.stale"'`` must hit.

    We can't shell out to grep in unit tests, but the contract is "exactly one
    row with action == action_item.stale", which is what reviewers will audit.
    """
    item = _seed_item(db_session)
    log_action_item_stale(
        db_session,
        item=item,
        stale_after_attempt=4,
        admin_notified=True,
        creator_audit_only=True,
    )

    rows = list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "action_item.stale")
        )
    )
    assert len(rows) == 1
    assert rows[0].payload_json["wake_count"] == 4


def test_log_action_item_stale_rejects_unassigned_item(db_session):
    item = _seed_item(db_session, owner_agent_id=None)

    # Sanity: the seed really produced an unassigned item.
    assert item.owner_agent_id is None

    with pytest.raises(ValueError, match="owner_agent_id"):
        log_action_item_stale(
            db_session,
            item=item,
            stale_after_attempt=4,
            admin_notified=True,
            creator_audit_only=True,
        )


def test_log_action_item_stale_rejects_when_last_woken_at_missing(db_session):
    item = _seed_item(db_session, last_woken_at=None)

    with pytest.raises(ValueError, match="last_woken_at"):
        log_action_item_stale(
            db_session,
            item=item,
            stale_after_attempt=4,
            admin_notified=True,
            creator_audit_only=True,
        )


@pytest.mark.parametrize("bad_attempt", [0, -1, -100])
def test_log_action_item_stale_rejects_non_positive_attempt(db_session, bad_attempt):
    item = _seed_item(db_session)

    with pytest.raises(ValueError, match="stale_after_attempt"):
        log_action_item_stale(
            db_session,
            item=item,
            stale_after_attempt=bad_attempt,
            admin_notified=True,
            creator_audit_only=True,
        )
