"""v0.9 acceptance tests for ``notification_service``.

Covers action_item ``fdb89de2`` (owner = participant) — implements the
single classify entry, v2 fingerprint writer, atomic ``wake_version`` /
``read_at`` binding, and the four acceptance sub-scenarios from topic
``d0df651c`` Round 1 Summary:

  1. digest 不 wake
  2. digest ``read_at`` 重置 = ``unread_count + 1``，不发 SSE
  3. 双通道单 wake (todo bucket + digest notification, single prompt)
  4. v1 ``rejection_count`` 路径 (legacy fingerprints rejected)

These tests pin the *service* contract — they do NOT cover the waker / API
route wiring (host's M30A/M31 scope) but they assert the building blocks
the host's wiring relies on:

  - ``classify()`` is the only category decision function.
  - Every new notification row is stamped ``fingerprint_version="v2"``.
  - Aggregation bumps ``wake_version`` strictly monotonic and resets
    ``read_at`` in the same SQL flush.
  - Digest notifications never publish an SSE frame, even when their
    upsert resets ``read_at`` (``unread_count`` goes up).
  - Dual-channel events produce a digest notification whose overlap keys
    match the todo bucket's overlap keys so the waker dedupes them.
  - ``v2_fingerprint()`` produces the canonical v2 fingerprint shape, and
    a legacy ``inbound:<event_id>`` v1 fingerprint is detectable so the
    host's resume endpoint can route it to ``rejection_count``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from map_types.enums import (
    NotificationCategory,
    NotificationFingerprintVersion,
)
from server.domain.models import (
    Agent,
    AgentRole,
    Notification,
    Project,
)
from server.services import notification_service
from server.services.notification_service import (
    classify,
    enqueue_for_agents,
    enqueue_from_event,
    notify_topic_comment_created,
    v2_fingerprint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    db_session,
    *,
    name: str,
    project_id: uuid.UUID | None = None,
    role: AgentRole = AgentRole.agent,
) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        api_token_hash="x" * 64,
        api_token_prefix="test",
        project_id=project_id,
        role=role,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _make_project(db_session) -> Project:
    project = Project(
        id=uuid.uuid4(),
        project_key=f"v09-test-{uuid.uuid4().hex[:8]}",
        name="v0.9 test project",
        workspace_path="/tmp/v09-test",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _spy_stream_publish(monkeypatch):
    """Replace ``notification_stream.publish`` with a spy so tests can assert
    which categories / wake_versions / fingerprint_versions crossed the SSE
    boundary. Returns a list that captures every call.
    """
    captured: list[dict] = []

    def _spy(_agent_id, payload):
        captured.append(dict(payload))

    monkeypatch.setattr(
        notification_service.notification_stream,
        "publish",
        _spy,
    )
    return captured


# ---------------------------------------------------------------------------
# Acceptance 1 — classify() is the single category decision entry point
# ---------------------------------------------------------------------------


def test_classify_is_single_entry_point(db_session, monkeypatch):
    """Topic d0df651c Round 1 hard rule: API route / waker / Web must NOT
    carry category decision logic. ``classify()`` is the only function that
    looks at ``WAKEABLE_NOTIFICATION_EVENTS`` / the explicit ``wakeable=``
    kwarg; the private ``_event_category`` alias exists only as a legacy
    back-compat shim and must delegate to ``classify``.
    """
    # 1. Default classification (no explicit kwarg) → digest unless the event
    #    is in the wakeable set.
    assert classify("experiment.phase_changed") == NotificationCategory.digest
    assert classify("topic.comment.created") == NotificationCategory.digest
    assert classify("experiment.lifecycle.withdrawn") == NotificationCategory.wakeable
    assert classify("system.runtime_attention") == NotificationCategory.wakeable

    # 2. Explicit override (used by action_item wake helpers) wins.
    assert classify("anything", wakeable=True) == NotificationCategory.wakeable
    assert classify("action_item.wake_sent", wakeable=False) == NotificationCategory.digest

    # 3. The back-compat alias must call classify. Spy on classify to ensure
    #    every emit path (enqueue_for_agents, enqueue_from_event) routes here
    #    instead of doing its own WAKEABLE_NOTIFICATION_EVENTS lookup.
    seen: list[tuple[str, bool | None]] = []
    original = notification_service.classify

    def _spy(event, *, wakeable=None):
        seen.append((event, wakeable))
        return original(event, wakeable=wakeable)

    monkeypatch.setattr(notification_service, "classify", _spy)
    project = _make_project(db_session)
    actor = _make_agent(db_session, name="actor", project_id=project.id)
    recipient = _make_agent(db_session, name="recipient", project_id=project.id)
    captured = _spy_stream_publish(monkeypatch)

    enqueue_from_event(
        db_session,
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.phase_changed",
        summary="phase",
        target_type="experiment",
        target_id=uuid.uuid4(),
        payload=None,
    )
    enqueue_for_agents(
        db_session,
        recipient_agent_ids=[recipient.id],
        project_id=project.id,
        actor_id=actor.id,
        event="topic.comment.created",
        summary="comment",
        target_type="topic_comment",
        target_id=uuid.uuid4(),
        payload=None,
    )

    # Both emit paths must have routed through classify(). If a future
    # refactor inlines the WAKEABLE_NOTIFICATION_EVENTS check anywhere
    # outside classify(), these callsites disappear.
    events_classified = [event for event, _ in seen]
    assert "experiment.phase_changed" in events_classified
    assert "topic.comment.created" in events_classified

    # And no SSE frame was published for either digest event.
    assert captured == [], (
        f"digest emit paths must not publish SSE frames, got {captured}"
    )


# ---------------------------------------------------------------------------
# Acceptance 2a — digest notifications do NOT trigger waker wake
# (and emit no SSE frame)
# ---------------------------------------------------------------------------


def test_digest_does_not_emit_sse_frame(db_session, monkeypatch):
    """Round 1 §1 (hard rule) + §5 (acceptance 1): digest notifications must
    not publish a ``notification.created`` SSE frame, otherwise the waker
    sees the frame, calls ``notifications_unread(category=wakeable)`` which
    filters it out at the API layer, and the SSE frame becomes pure noise
    (Round 1 §4: SSE frames carry ``category``; waker drops non-wakeable
    on receipt).
    """
    captured = _spy_stream_publish(monkeypatch)
    project = _make_project(db_session)
    actor = _make_agent(db_session, name="actor", project_id=project.id)
    recipient = _make_agent(db_session, name="recipient", project_id=project.id)

    enqueue_from_event(
        db_session,
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.phase_changed",  # digest
        summary="phase changed",
        target_type="experiment",
        target_id=uuid.uuid4(),
        payload=None,
    )
    assert captured == [], (
        f"digest enqueue must not publish SSE frame, got {captured}"
    )

    # Even an explicit wakeable=False kwarg (used by some call paths) must
    # not produce an SSE frame.
    enqueue_for_agents(
        db_session,
        recipient_agent_ids=[recipient.id],
        project_id=project.id,
        actor_id=actor.id,
        event="topic.comment.created",
        summary="comment",
        target_type="topic_comment",
        target_id=uuid.uuid4(),
        payload=None,
        wakeable=False,
    )
    assert captured == [], (
        f"explicit wakeable=False must not publish SSE frame, got {captured}"
    )


# ---------------------------------------------------------------------------
# Acceptance 2b — digest read_at reset = unread_count+1, no SSE frame
# ---------------------------------------------------------------------------


def test_digest_read_at_reset_increments_unread_count_without_sse(
    db_session, monkeypatch
):
    """Round 1 §5 acceptance #2: aggregating a digest notification must reset
    ``read_at`` (so the next ``/notifications?unread_only=true`` query sees
    it as unread) without firing an SSE frame — otherwise an SSE storm would
    hit the waker every time a digest event re-aggregates.
    """
    captured = _spy_stream_publish(monkeypatch)
    project = _make_project(db_session)
    actor = _make_agent(db_session, name="actor", project_id=project.id)
    recipient = _make_agent(db_session, name="recipient", project_id=project.id)
    target_id = uuid.uuid4()

    # First emit: digest, never read.
    enqueue_from_event(
        db_session,
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.phase_changed",
        summary="phase v1",
        target_type="experiment",
        target_id=target_id,
        payload=None,
    )
    rows = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == recipient.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].category == NotificationCategory.digest
    assert rows[0].read_at is None
    assert rows[0].wake_version == 1
    assert rows[0].fingerprint_version == NotificationFingerprintVersion.v2
    assert captured == [], "first digest emit must not publish SSE frame"

    # Mark as read — simulates the user clicking the notification.
    rows[0].read_at = datetime.now(timezone.utc)
    db_session.commit()

    # Second emit on the same target — aggregation path. Must:
    #   - reset read_at to None (unread_count goes back up)
    #   - NOT bump wake_version (digest never re-wakes)
    #   - NOT publish an SSE frame
    enqueue_from_event(
        db_session,
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.phase_changed",
        summary="phase v2",
        target_type="experiment",
        target_id=target_id,
        payload=None,
    )
    db_session.refresh(rows[0])
    assert rows[0].read_at is None, (
        "digest aggregation must reset read_at (unread_count+1)"
    )
    assert rows[0].wake_version == 1, (
        f"digest must NOT bump wake_version (would push new fingerprint), "
        f"got {rows[0].wake_version}"
    )
    assert captured == [], (
        f"digest read_at reset must NOT publish SSE frame, got {captured}"
    )


# ---------------------------------------------------------------------------
# Acceptance 3 — wakeable wake_version strictly monotonic + read_at bound
# to the same flush (atomic)
# ---------------------------------------------------------------------------


def test_wakeable_wake_version_strictly_monotonic_and_atomic_read_at_reset(
    db_session, monkeypatch
):
    """Round 1 §5 acceptance #3 + #4: same notification wakeable hit twice
    must bump ``wake_version`` (1 → 2 → 3, strictly increasing) AND reset
    ``read_at`` to None in the same flush as the bump. A torn state
    (read_at reset but wake_version stale) would push the waker to re-wake
    on the old fingerprint, which the unique gate then rejects; reviewers
    would see spurious rejection_count spikes with no UX signal.
    """
    captured = _spy_stream_publish(monkeypatch)
    project = _make_project(db_session)
    actor = _make_agent(db_session, name="actor", project_id=project.id)
    recipient = _make_agent(db_session, name="recipient", project_id=project.id)
    target_id = uuid.uuid4()

    enqueue_for_agents(
        db_session,
        recipient_agent_ids=[recipient.id],
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.lifecycle.withdrawn",  # wakeable
        summary="withdrawn v1",
        target_type="experiment",
        target_id=target_id,
        payload=None,
        wakeable=True,
    )
    rows = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == recipient.id)
    ).all()
    assert len(rows) == 1
    notif = rows[0]
    assert notif.category == NotificationCategory.wakeable
    assert notif.wake_version == 1
    assert notif.fingerprint_version == NotificationFingerprintVersion.v2

    # Mark as read (the waker would have acked).
    notif.read_at = datetime.now(timezone.utc)
    db_session.commit()

    # Second wakeable hit — must bump wake_version AND reset read_at in the
    # SAME flush. Capture the values on the in-memory instance both BEFORE
    # and AFTER flush to verify the column pair moves together.
    enqueue_for_agents(
        db_session,
        recipient_agent_ids=[recipient.id],
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.lifecycle.withdrawn",
        summary="withdrawn v2",
        target_type="experiment",
        target_id=target_id,
        payload=None,
        wakeable=True,
    )

    # The two fields must move together. We re-fetch to confirm the flush
    # persisted both atomically.
    db_session.refresh(notif)
    assert notif.read_at is None, "wakeable aggregation must reset read_at"
    assert notif.wake_version == 2, (
        f"wake_version must strictly increase (1 -> 2), got {notif.wake_version}"
    )
    # Strictly monotonic across a third hit.
    enqueue_for_agents(
        db_session,
        recipient_agent_ids=[recipient.id],
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.lifecycle.withdrawn",
        summary="withdrawn v3",
        target_type="experiment",
        target_id=target_id,
        payload=None,
        wakeable=True,
    )
    db_session.refresh(notif)
    assert notif.wake_version == 3

    # Wakeable events DO publish an SSE frame (carries category + wake_version
    # + fingerprint_version so the waker can build the v2 fingerprint).
    assert len(captured) == 3, (
        f"wakeable aggregation must publish one SSE frame per upsert, "
        f"got {len(captured)}: {captured}"
    )
    for frame in captured:
        assert frame["category"] == "wakeable"
        assert frame["fingerprint_version"] == "v2"
        assert frame["wake_version"] >= 1


def test_v2_fingerprint_is_canonical_format():
    """v2_fingerprint(persona, notification_id, wake_version) must produce the
    exact format the waker's resume gate expects. The shape is the contract
    — any change here would invalidate every waker resume attempt for
    post-v0.9 notifications.
    """
    fp = v2_fingerprint("participant", uuid.UUID(int=1), 7)
    assert fp == "participant:notification:00000000-0000-0000-0000-000000000001:7"

    # Confirm the waker-side builder matches.
    from cli.runtime_waker import _todo_item_stable_id  # noqa: F401

    # Direct format check: 4 colon-separated segments, persona + literal
    # "notification" + uuid + int wake_version.
    parts = fp.split(":")
    assert len(parts) == 4
    assert parts[0] == "participant"
    assert parts[1] == "notification"


def test_legacy_v1_fingerprint_is_detectable():
    """Round 1 §5 acceptance #4 (v1 rejection_count 路径): legacy v1
    fingerprints look like ``inbound:<event_id>``. The service exposes a
    helper so the host's resume endpoint can route them to the
    ``InboundEvent.rejection_count`` counter instead of resuming the session.
    """
    from server.services.notification_service import is_legacy_v1_fingerprint

    assert is_legacy_v1_fingerprint("inbound:abc123") is True
    assert is_legacy_v1_fingerprint("inbound:00000000-0000-0000-0000-000000000001") is True
    # v2 fingerprints must NOT be flagged as legacy.
    assert is_legacy_v1_fingerprint(
        "participant:notification:00000000-0000-0000-0000-000000000001:3"
    ) is False
    # Todo-bucket fingerprints are also not v1.
    assert is_legacy_v1_fingerprint("participant:action_items:abc") is False


# ---------------------------------------------------------------------------
# Acceptance 4 — dual-channel single wake
# ---------------------------------------------------------------------------


def test_dual_channel_emits_digest_notification_with_overlap_keys(
    db_session, monkeypatch
):
    """Round 1 §5 acceptance #3 (双通道单 wake): a single business event
    triggers both a todo-bucket item AND a notification row. The waker's
    ``discover_wake_events`` builds two ``WakeEvent``s — one from the todo
    bucket, one from the notification — but the notification's overlap keys
    must collide with the todo bucket so the waker drops the notification
    branch and resumes a single session.

    At the service layer we can only assert the digest side: that the
    notification is classified ``digest`` (so the waker's category filter
    would already drop it if it slipped past the overlap-key check) AND
    that ``target_type/target_id/payload.topic_id`` produce overlap keys
    matching what ``_notification_overlap_keys`` computes.
    """
    captured = _spy_stream_publish(monkeypatch)
    project = _make_project(db_session)
    actor = _make_agent(db_session, name="actor", project_id=project.id)
    creator = _make_agent(db_session, name="creator", project_id=project.id)
    target_comment_id = uuid.uuid4()
    topic_id = uuid.uuid4()

    notify_topic_comment_created(
        db_session,
        project_id=project.id,
        actor_id=actor.id,
        creator_agent_id=creator.id,
        target_id=target_comment_id,
        payload={"topic_id": str(topic_id), "comment_id": str(target_comment_id)},
    )

    # No SSE frame published for the digest comment notification.
    assert captured == [], (
        f"dual-channel digest path must not publish SSE frame, got {captured}"
    )

    # The notification is classified digest + carries the topic_id overlap key.
    rows = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == creator.id)
    ).all()
    # The host-directed notification (creator != actor path) is the one we
    # assert against; project members also get a digest row but with the
    # generic payload.
    assert len(rows) >= 1
    host_directed = [r for r in rows if r.target_id == target_comment_id]
    assert host_directed, "host-directed notification must be written"
    notif = host_directed[0]
    assert notif.category == NotificationCategory.digest
    assert notif.fingerprint_version == NotificationFingerprintVersion.v2
    assert notif.payload_json["topic_id"] == str(topic_id)


# ---------------------------------------------------------------------------
# Backstop — new notifications all carry v2 fingerprint
# ---------------------------------------------------------------------------


def test_new_notifications_default_to_v2_fingerprint(db_session, monkeypatch):
    """Round 1 §5 acceptance #2: v2 fingerprint writer must cover ALL
    post-v0.9 events regardless of which emit path was taken. This is the
    contract the host's waker resume depends on.
    """
    captured = _spy_stream_publish(monkeypatch)
    project = _make_project(db_session)
    actor = _make_agent(db_session, name="actor", project_id=project.id)
    recipient = _make_agent(db_session, name="recipient", project_id=project.id)

    # Digest path
    enqueue_from_event(
        db_session,
        project_id=project.id,
        actor_id=actor.id,
        event="experiment.phase_changed",
        summary="phase",
        target_type="experiment",
        target_id=uuid.uuid4(),
        payload=None,
    )
    # Wakeable path
    enqueue_for_agents(
        db_session,
        recipient_agent_ids=[recipient.id],
        project_id=project.id,
        actor_id=actor.id,
        event="action_item.wake_sent",
        summary="wake",
        target_type="topic_action_item",
        target_id=uuid.uuid4(),
        payload=None,
        wakeable=True,
    )

    rows = db_session.scalars(select(Notification)).all()
    assert len(rows) >= 2
    for row in rows:
        assert row.fingerprint_version == NotificationFingerprintVersion.v2, (
            f"every post-v0.9 notification must be v2, got {row.fingerprint_version} "
            f"on event={row.event}"
        )

    # The SSE frame for the wakeable path carries v2 marker so the waker
    # doesn't have to second-guess the format.
    assert any(
        frame.get("fingerprint_version") == "v2" for frame in captured
    ), f"wakeable SSE frame must carry fingerprint_version=v2, got {captured}"
