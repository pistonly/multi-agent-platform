import uuid
from datetime import UTC, datetime

from map_types.enums import NotificationCategory, NotificationFingerprintVersion
from sqlalchemy import event, func, or_, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Notification, TopicActionItem
from server.services import notification_stream
from server.services.errors import ForbiddenError, NotFoundError

# Phase 2 D2: persona agent names used by emit_kind to resolve wake-kind
# recipients. These are the canonical MAP persona agents bound to a project;
# the wake kinds (pending_review / pending_result_review / topic_lifecycle /
# etc.) target one or more of these so the waker can differentiate lifecycle
# events from generic notifications. Falls back to no recipients if a project
# has not yet bound a given persona (early onboarding is graceful).
PERSONA_AGENT_NAMES: dict[str, str] = {
    "host": "multi-agents-platform-host",
    "participant": "multi-agents-platform-participant",
    "reviewer": "multi-agents-platform-reviewer",
}

"""Wakeable 事件显式白名单——v0.9 用以替代显式 runtime feature flag。

等价性论证（与 topic d0df651c resolve decision 一致）：
- ``NotificationCategory.digest`` 是 schema 默认值，等价于「feature flag off」（不触发 waker）
- 仅本集合显式列出的事件走 ``wakeable``，等价于「feature flag 显式开启」
- 不启用 ``system.runtime_attention``（schema 占位），等价于「feature flag 不引入新 wakeable 入口」

新增 wakeable 事件必须在此集合内显式声明并经过代码评审，避免 schema/runtime config 双源漂移。
"""

WAKEABLE_NOTIFICATION_EVENTS: set[str] = {
    "experiment.lifecycle.withdrawn",
    "experiment.lifecycle.cancelled",
    "review_item.status_changed",
    "system.runtime_attention",
    "topic.lifecycle.closed",
    "topic.lifecycle.reopened",
    "action_item.wake_sent",
    "action_item.stale",
}


def _emit_created(
    recipient_ids: list[uuid.UUID],
    notification_ids: list[uuid.UUID],
    *,
    event: str,
    categories: list[NotificationCategory] | None = None,
    wake_versions: list[int] | None = None,
    fingerprint_versions: list[NotificationFingerprintVersion] | None = None,
) -> None:
    """Publish ``notification.created`` SSE frames for wakeable notifications.

    v0.9 PRD §7.2: the waker drops digest notifications at the SSE frame layer,
    so digest rows must NOT trigger a publish here. ``enqueue_for_agents`` and
    ``enqueue_from_event`` pre-filter the recipient list to wakeable rows
    before calling us; ``categories`` is kept parallel so SSE subscribers can
    see which category they received without a second round-trip.
    """
    if categories is None:
        categories = [NotificationCategory.wakeable] * len(notification_ids)
    if wake_versions is None:
        wake_versions = [1] * len(notification_ids)
    if fingerprint_versions is None:
        fingerprint_versions = [NotificationFingerprintVersion.v2] * len(notification_ids)
    for recipient_id, notification_id, category, wake_version, fp_version in zip(
        recipient_ids,
        notification_ids,
        categories,
        wake_versions,
        fingerprint_versions,
        strict=True,
    ):
        if category != NotificationCategory.wakeable:
            continue
        notification_stream.publish(
            recipient_id,
            {
                "type": "notification.created",
                "event": event,
                "notification_id": str(notification_id),
                "category": category.value,
                "wake_version": wake_version,
                "fingerprint_version": fp_version.value,
            },
        )


_PENDING_SSE_KEY = "map_pending_notification_created_sse"
_PendingSseFrame = tuple[
    list[uuid.UUID],
    list[uuid.UUID],
    str,
    list[NotificationCategory],
    list[int],
    list[NotificationFingerprintVersion],
]


def _queue_created_after_commit(
    db: Session,
    recipient_ids: list[uuid.UUID],
    notification_ids: list[uuid.UUID],
    *,
    event: str,
    categories: list[NotificationCategory],
    wake_versions: list[int],
    fingerprint_versions: list[NotificationFingerprintVersion],
) -> None:
    """Publish notification SSE frames only after the DB transaction commits."""
    if not notification_ids:
        return
    pending = db.info.setdefault(_PENDING_SSE_KEY, [])
    pending.append(
        (
            list(recipient_ids),
            list(notification_ids),
            event,
            list(categories),
            list(wake_versions),
            list(fingerprint_versions),
        )
    )


@event.listens_for(Session, "after_commit")
def _publish_pending_created_after_commit(db: Session) -> None:
    pending: list[_PendingSseFrame] = db.info.pop(_PENDING_SSE_KEY, [])
    for (
        recipient_ids,
        notification_ids,
        event_name,
        categories,
        wake_versions,
        fingerprint_versions,
    ) in pending:
        _emit_created(
            recipient_ids,
            notification_ids,
            event=event_name,
            categories=categories,
            wake_versions=wake_versions,
            fingerprint_versions=fingerprint_versions,
        )


@event.listens_for(Session, "after_rollback")
def _discard_pending_created_after_rollback(db: Session) -> None:
    db.info.pop(_PENDING_SSE_KEY, None)


def _resolve_persona_agent_ids(
    db: Session, project_id: uuid.UUID, personas: list[str]
) -> list[uuid.UUID]:
    """Resolve persona names to Agent.id within a project.

    Returns an empty list if no persona matches (e.g. project hasn't bound
    that persona yet) so callers can treat it as a no-op rather than a 500.
    """
    names = [PERSONA_AGENT_NAMES[p] for p in personas if p in PERSONA_AGENT_NAMES]
    if not names:
        return []
    rows = db.scalars(
        select(Agent).where(Agent.name.in_(names), Agent.project_id == project_id)
    ).all()
    return [agent.id for agent in rows]


def classify(event: str, *, wakeable: bool | None = None) -> NotificationCategory:
    """Single public entry point for category decisions.

    Per topic d0df651c Round 1 (1b) hard rule: API route / waker / Web MUST NOT
    carry any category decision logic — every notification row's ``category``
    value must come through this function. The explicit ``wakeable`` kwarg is
    reserved for the wakeable-only ``action_item.*`` / ``system.runtime_attention``
    paths where the caller has authoritative knowledge (see
    ``notify_owner_action_item_wake`` etc.).
    """
    if wakeable is not None:
        return NotificationCategory.wakeable if wakeable else NotificationCategory.digest
    if event in WAKEABLE_NOTIFICATION_EVENTS:
        return NotificationCategory.wakeable
    return NotificationCategory.digest


def _event_category(event: str, *, wakeable: bool | None = None) -> NotificationCategory:
    """Backwards-compat alias; delegates to :func:`classify`."""
    return classify(event, wakeable=wakeable)


def v2_fingerprint(persona: str, notification_id: uuid.UUID, wake_version: int) -> str:
    """Build the canonical v2 fingerprint for a notification wake.

    Format from topic d0df651c Round 1 §2: ``{persona}:notification:{notification_id}:{wake_version}``.
    The waker's resume gate relies on this exact shape; v1 fingerprints
    (``inbound:<event_id>``) are rejected and counted via
    ``InboundEvent.rejection_count`` (see M30A acceptance §4).
    """
    return f"{persona}:notification:{notification_id}:{wake_version}"


def is_legacy_v1_fingerprint(fingerprint: str) -> bool:
    """Return True if ``fingerprint`` uses the pre-v0.9 ``inbound:<event_id>``
    shape so the host's resume endpoint can route it to the v1 rejection
    counter instead of resuming a session.

    v0.9 fingerprints are namespace-prefixed (``{persona}:notification:...``
    or ``{persona}:{todo_bucket}:...``) so they never start with ``inbound:``.
    """
    return fingerprint.startswith("inbound:")


def _group_target(
    *,
    event: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
) -> tuple[str, str]:
    data = payload or {}
    if event == "topic.comment.created" and data.get("topic_id"):
        return "topic", str(data["topic_id"])
    if event == "comment.created" and data.get("experiment_id"):
        return "experiment", str(data["experiment_id"])
    if target_id is not None:
        return target_type, str(target_id)
    for key in ("topic_id", "experiment_id", "id"):
        if data.get(key):
            inferred_type = "topic" if key == "topic_id" else "experiment" if key == "experiment_id" else target_type
            return inferred_type, str(data[key])
    return target_type, "none"


def _group_key(
    *,
    recipient_agent_id: uuid.UUID,
    project_id: uuid.UUID | None,
    event: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
) -> str:
    group_target_type, group_target_id = _group_target(
        event=event,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )
    project = str(project_id) if project_id is not None else "global"
    return f"recipient:{recipient_agent_id}:project:{project}:{group_target_type}:{group_target_id}:{event}"


def _upsert_notification(
    db: Session,
    *,
    recipient_agent_id: uuid.UUID,
    project_id: uuid.UUID | None,
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
    category: NotificationCategory,
) -> Notification:
    now = datetime.now(UTC)
    group_key = _group_key(
        recipient_agent_id=recipient_agent_id,
        project_id=project_id,
        event=event,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )
    existing = db.scalar(
        select(Notification)
        .where(
            Notification.recipient_agent_id == recipient_agent_id,
            Notification.group_key == group_key,
        )
        .order_by(Notification.updated_at.desc(), Notification.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        previous_wake_version = existing.wake_version or 1
        previous_fingerprint_version = existing.fingerprint_version
        existing.event = event
        existing.summary = summary
        existing.target_type = target_type
        existing.target_id = target_id
        existing.payload_json = payload
        existing.category = category
        existing.event_count = (existing.event_count or 1) + 1
        existing.last_event_at = now
        existing.updated_at = now
        if existing.first_event_at is None:
            existing.first_event_at = existing.created_at
        # v0.9 acceptance #3 + #4: wake_version monotonic + read_at bound to
        # the same flush. Both fields are assigned before db.flush() so a
        # single SQL UPDATE ships both columns. Same for digest: the read_at
        # reset is what drives unread_count+1, but digest must NOT bump
        # wake_version (it would push a new fingerprint that the waker would
        # then re-wake — digest is non-actionable by definition).
        if category == NotificationCategory.wakeable:
            existing.wake_version = previous_wake_version + 1
            existing.read_at = None
        else:
            existing.read_at = None
        # Preserve fingerprint_version on upsert: a legacy v1 row stays v1 so
        # the waker's rejection_count path can keep counting it; fresh v2 rows
        # never downgrade.
        if previous_fingerprint_version is not None:
            existing.fingerprint_version = previous_fingerprint_version
        # Invariant: wake_version is strictly monotonic. Caught here so a
        # future refactor cannot silently violate it.
        assert existing.wake_version >= previous_wake_version, (
            f"wake_version went backwards: {previous_wake_version} -> {existing.wake_version}"
        )
        db.flush()
        return existing

    notification = Notification(
        recipient_agent_id=recipient_agent_id,
        project_id=project_id,
        event=event,
        summary=summary,
        target_type=target_type,
        target_id=target_id,
        payload_json=payload,
        category=category,
        group_key=group_key,
        wake_version=1,
        fingerprint_version=NotificationFingerprintVersion.v2,
        event_count=1,
        first_event_at=now,
        last_event_at=now,
        updated_at=now,
    )
    db.add(notification)
    db.flush()
    return notification


def emit_kind(
    db: Session,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    personas: list[str],
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID,
    payload: dict | None,
    wakeable: bool | None = None,
    commit: bool = True,
) -> list[uuid.UUID]:
    """Insert one Notification per persona agent + SSE publish.

    Phase 2 D2 entry point for kind-specific wake events. Distinct from
    ``enqueue_from_event`` (broadcast to all project agents) — here the
    recipient set is narrowed to persona agents (host / participant /
    reviewer) so the waker can differentiate lifecycle wake kinds from
    the generic ``notification`` bucket.

    Payload is enriched with ``kind`` (derived from the event name's middle
    segment, e.g. ``topic.lifecycle.closed`` → ``lifecycle``) so the waker
    can build a kind-specific fingerprint without re-deriving from the
    Notification row.
    """
    recipient_ids = _resolve_persona_agent_ids(db, project_id, personas)
    if not recipient_ids:
        return []
    enriched = dict(payload or {})
    parts = event.split(".")
    if len(parts) >= 2 and "kind" not in enriched:
        enriched["kind"] = ".".join(parts[:-1])
    return enqueue_for_agents(
        db,
        recipient_agent_ids=recipient_ids,
        project_id=project_id,
        actor_id=actor_id,
        event=event,
        summary=summary,
        target_type=target_type,
        target_id=target_id,
        payload=enriched,
        wakeable=wakeable,
        commit=commit,
    )


def _recipients_for_project(db: Session, project_id: uuid.UUID | None, exclude_agent_id: uuid.UUID) -> list[Agent]:
    if project_id is None:
        return []
    stmt = select(Agent).where(
        or_(
            Agent.project_id == project_id,
            Agent.role == AgentRole.admin,
        )
    )
    return [agent for agent in db.scalars(stmt) if agent.id != exclude_agent_id]


def enqueue_from_event(
    db: Session,
    *,
    project_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
    exclude_recipient_ids: set[uuid.UUID] | None = None,
    wakeable: bool | None = None,
    commit: bool = True,
) -> list[uuid.UUID]:
    """Write in-app notifications for project agents (and admins), excluding the actor.

    ``commit=False`` 时只 ``flush`` 不 ``commit``——供需要把通知写入与调用方
    自身业务变更绑在同一事务内的场景。调用方负责最终 commit。
    """
    skip = exclude_recipient_ids or set()
    recipients = [
        agent
        for agent in _recipients_for_project(db, project_id, actor_id)
        if agent.id not in skip
    ]
    if not recipients:
        return []

    notification_ids: list[uuid.UUID] = []
    recipient_ids: list[uuid.UUID] = []
    categories: list[NotificationCategory] = []
    wake_versions: list[int] = []
    fingerprint_versions: list[NotificationFingerprintVersion] = []
    category = classify(event, wakeable=wakeable)
    for recipient in recipients:
        notification = _upsert_notification(
            db,
            recipient_agent_id=recipient.id,
            project_id=project_id,
            summary=summary,
            event=event,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            category=category,
        )
        notification_ids.append(notification.id)
        recipient_ids.append(recipient.id)
        categories.append(notification.category)
        wake_versions.append(notification.wake_version)
        fingerprint_versions.append(notification.fingerprint_version)
    _queue_created_after_commit(
        db,
        recipient_ids,
        notification_ids,
        event=event,
        categories=categories,
        wake_versions=wake_versions,
        fingerprint_versions=fingerprint_versions,
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return notification_ids


def notify_topic_comment_created(
    db: Session,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    target_id: uuid.UUID,
    payload: dict | None,
) -> list[uuid.UUID]:
    """Broadcast to project agents; host gets a separate directed notification (no duplicate)."""
    event = "topic.comment.created"
    base_payload = dict(payload or {})
    notification_ids = enqueue_from_event(
        db,
        project_id=project_id,
        actor_id=actor_id,
        event=event,
        summary="话题新评论",
        target_type="topic_comment",
        target_id=target_id,
        payload=base_payload,
        exclude_recipient_ids={creator_agent_id} if creator_agent_id != actor_id else None,
    )
    if creator_agent_id != actor_id:
        notification_ids.extend(
            enqueue_for_agents(
                db,
                recipient_agent_ids=[creator_agent_id],
                project_id=project_id,
                actor_id=actor_id,
                event=event,
                summary="【主持】话题新评论待回复",
                target_type="topic_comment",
                target_id=target_id,
                payload={**base_payload, "host_directed": True},
            )
        )
    return notification_ids


def enqueue_for_agents(
    db: Session,
    *,
    recipient_agent_ids: list[uuid.UUID],
    project_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
    wakeable: bool | None = None,
    exclude_actor: bool = True,
    commit: bool = True,
) -> list[uuid.UUID]:
    """Write in-app notifications for specific agents (e.g. @mentions).

    ``commit=False`` 时只 ``flush``——供需要把通知与调用方业务变更绑在同一
    事务内的场景（如 mention 处理）。调用方负责最终 commit。
    """
    notification_ids: list[uuid.UUID] = []
    recipient_ids: list[uuid.UUID] = []
    categories: list[NotificationCategory] = []
    wake_versions: list[int] = []
    fingerprint_versions: list[NotificationFingerprintVersion] = []
    category = classify(event, wakeable=wakeable)
    for recipient_id in recipient_agent_ids:
        if exclude_actor and recipient_id == actor_id:
            continue
        notification = _upsert_notification(
            db,
            recipient_agent_id=recipient_id,
            project_id=project_id,
            event=event,
            summary=summary,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            category=category,
        )
        notification_ids.append(notification.id)
        recipient_ids.append(recipient_id)
        categories.append(notification.category)
        wake_versions.append(notification.wake_version)
        fingerprint_versions.append(notification.fingerprint_version)
    if notification_ids:
        _queue_created_after_commit(
            db,
            recipient_ids,
            notification_ids,
            event=event,
            categories=categories,
            wake_versions=wake_versions,
            fingerprint_versions=fingerprint_versions,
        )
        if commit:
            db.commit()
        else:
            db.flush()
    return notification_ids


def list_for_agent(
    db: Session,
    agent: Agent,
    *,
    unread_only: bool = False,
    category: NotificationCategory | None = None,
    target_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int]:
    filters = [Notification.recipient_agent_id == agent.id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))
    if category is not None:
        filters.append(Notification.category == category)
    if target_type is not None:
        filters.append(Notification.target_type == target_type)
    total = db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Notification)
            .where(*filters)
            .order_by(Notification.updated_at.desc(), Notification.created_at.desc())
            .offset(offset)
            .limit(min(limit, 200))
        )
    )
    return rows, total


def count_unread(
    db: Session,
    agent: Agent,
    *,
    category: NotificationCategory | None = None,
    target_type: str | None = None,
) -> int:
    filters = [Notification.recipient_agent_id == agent.id, Notification.read_at.is_(None)]
    if category is not None:
        filters.append(Notification.category == category)
    if target_type is not None:
        filters.append(Notification.target_type == target_type)
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(*filters)
        )
        or 0
    )


def mark_agent_mentioned_notifications_read_no_commit(
    db: Session,
    *,
    mentions: list,
    now: datetime | None = None,
) -> int:
    """Mark ``agent.mentioned`` notifications read when their mention is dismissed."""
    from server.domain.models import Mention

    if not mentions:
        return 0
    now = now or datetime.now(UTC)
    touched = 0
    for mention in mentions:
        if not isinstance(mention, Mention):
            continue
        rows = list(
            db.scalars(
                select(Notification).where(
                    Notification.recipient_agent_id == mention.mentioned_agent_id,
                    Notification.event == "agent.mentioned",
                    Notification.read_at.is_(None),
                    Notification.target_id == mention.source_id,
                )
            )
        )
        for row in rows:
            row.read_at = now
            touched += 1
    return touched


def mark_read(db: Session, agent: Agent, notification_id: uuid.UUID) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise NotFoundError("Notification not found")
    if notification.recipient_agent_id != agent.id:
        raise ForbiddenError("Cannot mark another agent's notification")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_read(db: Session, agent: Agent) -> int:
    now = datetime.now(UTC)
    rows = list(
        db.scalars(
            select(Notification).where(
                Notification.recipient_agent_id == agent.id,
                Notification.read_at.is_(None),
            )
        )
    )
    for row in rows:
        row.read_at = now
    if rows:
        db.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# action_item wake / stale notification helpers (experiment B, plan §3 #2 #3)
# ---------------------------------------------------------------------------
#
# The runtime-waker calls ``mark_wake_sent`` / ``mark_stale`` via the API,
# but those endpoints only mutate the action_item + write the audit row.
# They do NOT post any in-app notification — by design, the wakeable
# signals the assignee / admin sees live here, alongside the audit row.
#
# Why split this out instead of folding into the endpoint wrappers:
# - Keeps ``action_item_service`` / ``topic_service`` free of notification
#   service imports (service layering: audit + state in service, fan-out in
#   notification service).
# - Makes the actor / recipient policy explicit per plan section §3 #2 #3:
#   wake goes to the assignee; stale goes to admins; creator is intentionally
#   skipped (audit-only path, see I6).
# ---------------------------------------------------------------------------


def _resolve_admin_agent_ids(
    db: Session,
    *,
    exclude_agent_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Return the list of admin Agent ids in the system.

    Admin role is global in this codebase (no per-project scoping), so a
    single query against ``Agent.role == AgentRole.admin`` is sufficient.
    ``exclude_agent_id`` filters the assignee out so the owner does not get
    a duplicate notification through the admin path when the assignee is
    themselves an admin.
    """
    rows = db.scalars(select(Agent.id).where(Agent.role == AgentRole.admin)).all()
    if exclude_agent_id is None:
        return list(rows)
    return [row for row in rows if row != exclude_agent_id]


def notify_owner_action_item_wake(
    db: Session,
    *,
    action_item: TopicActionItem,
) -> list[uuid.UUID]:
    """Send a wakeable notification to the assignee after a wake bump.

    Plan §3 wake path: the runtime-waker wakes the owner at T+24h / T+72h /
    every 7d up to 4 times. Each bump surfaces here as an in-app notification
    so the assignee sees a wakeable signal in their todos (independent of
    whether they happen to be looking at the action_item list right now).
    Falls through silently when the item has no owner (shouldn't happen —
    ``mark_wake_sent`` already rejected unassigned items upstream — but the
    guard is here for defence-in-depth).
    """
    if action_item.owner_agent_id is None:
        return []
    payload: dict[str, object] = {
        "action_item_id": str(action_item.id),
        "topic_id": str(action_item.topic_id),
        "wake_count": action_item.wake_count,
    }
    if action_item.last_woken_at is not None:
        payload["last_woken_at"] = action_item.last_woken_at.isoformat()
    return enqueue_for_agents(
        db,
        recipient_agent_ids=[action_item.owner_agent_id],
        project_id=action_item.project_id,
        actor_id=action_item.owner_agent_id,
        event="action_item.wake_sent",
        summary=f"待办提醒：{action_item.title}（第 {action_item.wake_count} 次）",
        target_type="topic_action_item",
        target_id=action_item.id,
        payload=payload,
        wakeable=True,
        exclude_actor=False,
    )


def notify_admin_action_item_stale(
    db: Session,
    *,
    action_item: TopicActionItem,
) -> list[uuid.UUID]:
    """Send a wakeable notification to every admin after a stale transition.

    Plan §3 #2 (3c admin 优先): admin is the most stable收口 because the
    creator may have been deactivated / changed roles / left the project.
    The owner is explicitly excluded — if the owner happens to be an admin
    they'd otherwise get a duplicate notification through both paths, and
    the audit row + plan §3 #3 (3c creator audit-only) policy says admin
    notification should not double as the owner's channel.

    Plan §3 #3 (creator audit-only): the creator is intentionally NOT in
    this recipient set. They can find the stale event via ``action_items``
    or the audit history, but they do NOT get a wake notification (per I6).
    """
    admin_ids = _resolve_admin_agent_ids(
        db, exclude_agent_id=action_item.owner_agent_id
    )
    if not admin_ids:
        return []
    payload: dict[str, object] = {
        "action_item_id": str(action_item.id),
        "topic_id": str(action_item.topic_id),
        "wake_count": action_item.wake_count,
    }
    if action_item.stale_at is not None:
        payload["stale_at"] = action_item.stale_at.isoformat()
    # actor_id is unused because exclude_actor=False — every admin in the
    # resolved list gets the notification regardless. We still need to pass
    # a UUID-shaped value to satisfy the signature, so use the first admin
    # or the owner as a stable label.
    actor_id = action_item.owner_agent_id or admin_ids[0]
    return enqueue_for_agents(
        db,
        recipient_agent_ids=admin_ids,
        project_id=action_item.project_id,
        actor_id=actor_id,
        event="action_item.stale",
        summary=f"行动项已 stale：{action_item.title}",
        target_type="topic_action_item",
        target_id=action_item.id,
        payload=payload,
        wakeable=True,
        exclude_actor=False,
    )
