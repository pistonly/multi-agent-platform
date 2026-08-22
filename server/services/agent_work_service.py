"""Unified agent work snapshot (whoami + topic-progress + todos + wakeable notifications)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from map_types.enums import AgentRole, NotificationCategory
from sqlalchemy.orm import Session

from server.domain.models import Agent
from server.domain.schemas import (
    AgentRead,
    AgentWorkRead,
    AgentWorkSummaryRead,
    BucketVisibility,
    NotificationListRead,
    NotificationRead,
    SummaryBucket,
    SummaryBucketItem,
    SummaryBucketKind,
    TopicProgressListRead,
)
from server.services import notification_service, todo_service, topic_progress_service
from server.services import project_service as svc
from server.services import topic_work_item_service as work_items

_BUCKET_KIND_VISIBILITY: dict[SummaryBucketKind, BucketVisibility] = {
    "mention": "all",
    "round_ack": "all",
    "pending_reply": "all",
    "explicit_only": "host_only",
    "informational_only": "host_only",
    "action_items": "host_only",
}


def _is_host_persona(agent: Agent) -> bool:
    """A persona 'sees host-only buckets' when they are the host (admin
    always sees everything). Persona identity follows ``Agent.persona``
    (trailing ``-host`` name segment), so bootstrap projects'
    ``<project_key>-host`` agents count too.
    """
    return agent.role == AgentRole.admin or agent.persona == "host"


def _make_item(*, kind: SummaryBucketKind, topic_id=None, topic_title=None, excerpt=None, updated_at=None) -> SummaryBucketItem:
    return SummaryBucketItem(
        kind=kind, topic_id=topic_id, topic_title=topic_title, excerpt=excerpt, updated_at=updated_at
    )


def _bucket(
    *,
    kind: SummaryBucketKind,
    items: Iterable[SummaryBucketItem],
    top_excerpt: str | None = None,
) -> SummaryBucket:
    items = list(items)
    return SummaryBucket(
        kind=kind,
        count=len(items),
        visibility=_BUCKET_KIND_VISIBILITY[kind],
        items=items[:5],
        top_excerpt=top_excerpt or (items[0].excerpt if items else None),
    )


def get_agent_work(
    db: Session,
    agent: Agent,
    *,
    notification_limit: int = 50,
    notification_category: NotificationCategory | None = NotificationCategory.wakeable,
) -> AgentWorkRead:
    project_key: str | None = None
    if agent.project_id is not None:
        project_key = svc.get_project(db, agent.project_id).project_key

    agent_read = AgentRead(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        project_id=agent.project_id,
        project_key=project_key,
        created_at=agent.created_at,
    )

    bundle = work_items.topic_work_items_bundle_for_agent(db, agent)
    todos = todo_service.get_todos(db, agent, bundle=bundle)
    topic_progress = topic_progress_service.list_topic_progress_for_agent(
        db, agent, bundle=bundle
    )
    # fs source-of-truth: map/ 话题的文件存在性待办并入统一快照，
    # waker（simple-waker 轮询本端点）由此被 FS 待办唤醒，无第二套规则。
    from server.services import fs_source_service

    fs_progress = fs_source_service.fs_topic_progress_for_agent(db, agent)
    if fs_progress:
        merged = list(topic_progress.items) + fs_progress
        topic_progress = TopicProgressListRead(items=merged, total=len(merged))

    notif_items, notif_total = notification_service.list_for_agent(
        db,
        agent,
        unread_only=True,
        category=notification_category,
        limit=notification_limit,
        offset=0,
    )
    unread_count = notification_service.count_unread(
        db,
        agent,
        category=notification_category,
    )
    notifications = NotificationListRead(
        items=[NotificationRead.model_validate(n) for n in notif_items],
        total=notif_total,
        unread_count=unread_count,
    )

    source = None
    if agent.project_id is not None:
        source = fs_source_service.content_source_meta(
            db, svc.get_project(db, agent.project_id)
        )

    return AgentWorkRead(
        agent=agent_read,
        topic_progress=topic_progress,
        todos=todos,
        notifications=notifications,
        source=source,
    )


def get_agent_work_summary(
    db: Session,
    agent: Agent,
    *,
    include_all_personas: bool = False,
    topics_limit: int = 10,
    experiments_limit: int = 5,
) -> AgentWorkSummaryRead:
    """Compact 6-bucket by_kind summary of the agent's work.

    Buckets: mention, round_ack, pending_reply, explicit_only,
    informational_only, action_items. Persona filtering drops host-only
    buckets for non-host personas unless ``include_all_personas=True``.
    Truncation caps how many topics / experiments contribute per bucket to
    keep the summary card small.
    """
    project_key: str | None = None
    if agent.project_id is not None:
        project_key = svc.get_project(db, agent.project_id).project_key
    agent_read = AgentRead(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        project_id=agent.project_id,
        project_key=project_key,
        created_at=agent.created_at,
    )

    bundle = work_items.topic_work_items_bundle_for_agent(db, agent)
    todos = todo_service.get_todos(db, agent, bundle=bundle)
    items = bundle.items

    bucket_items: dict[SummaryBucketKind, list[SummaryBucketItem]] = defaultdict(list)

    # topic-derived work items (mention / round_ack / pending_reply)
    for w in items:
        if w.kind == "mention":
            bucket_items["mention"].append(
                _make_item(
                    kind="mention",
                    topic_id=w.topic_id,
                    topic_title=w.topic_title,
                    excerpt=w.excerpt,
                    updated_at=w.created_at,
                )
            )
        elif w.kind == "round_ack":
            bucket_items["round_ack"].append(
                _make_item(
                    kind="round_ack",
                    topic_id=w.topic_id,
                    topic_title=w.topic_title,
                    excerpt=w.excerpt,
                    updated_at=w.created_at,
                )
            )
        elif w.kind == "pending_topic_reply":
            bucket_items["pending_reply"].append(
                _make_item(
                    kind="pending_reply",
                    topic_id=w.topic_id,
                    topic_title=w.topic_title,
                    excerpt=w.excerpt,
                    updated_at=w.created_at,
                )
            )

    # mentions that didn't come through topic work items (experiment-scope)
    topic_mention_ids = {w.topic_id for w in items if w.kind == "mention"}
    for m in todos.mentions:
        if m.topic_id is None or m.topic_id in topic_mention_ids:
            continue
        bucket_items["mention"].append(
            _make_item(
                kind="mention",
                topic_id=m.topic_id,
                excerpt=m.excerpt,
                updated_at=m.created_at,
            )
        )

    # round_ack todo mirror
    for r in todos.pending_round_acks:
        bucket_items["round_ack"].append(
            _make_item(
                kind="round_ack",
                topic_id=r.topic_id,
                topic_title=r.topic_title,
                excerpt=r.summary_excerpt,
                updated_at=r.updated_at,
            )
        )

    # pending_reply todo mirror
    for reply in todos.pending_topic_replies:
        bucket_items["pending_reply"].append(
            _make_item(
                kind="pending_reply",
                topic_id=reply.topic_id,
                topic_title=reply.topic_title,
                excerpt=reply.excerpt,
                updated_at=reply.created_at,
            )
        )

    # explicit_only: host's lifecycle action items (visible only on host persona
    # by default; persona filter applies below).
    #
    # f873c287 I1(f) partition boundary: ``informational_only`` experiments
    # (phase_owner != host, actions=[], blocked_on is set) do NOT enter
    # the obligation partition (``explicit_only``); they route to
    # ``informational_only`` so the waker treats them as read-only noise.
    #
    # f873c287 I1(e): both bucket excerpts share the ``experiment:{phase}:
    # {phase_owner}`` shape so the by_owner breakdown algorithm below can
    # count them uniformly.
    for e in todos.my_open_experiments:
        owner_value = e.phase_owner.value
        phase_value = e.phase.value
        if getattr(e, "informational_only", False):
            bucket_items["informational_only"].append(
                _make_item(
                    kind="informational_only",
                    topic_id=None,
                    topic_title=e.title,
                    excerpt=(
                        f"experiment:{phase_value}:{owner_value}"
                        f":blocked={e.blocked_on}"
                        if e.blocked_on
                        else f"experiment:{phase_value}:{owner_value}:waiting"
                    ),
                    updated_at=e.updated_at,
                )
            )
            continue
        bucket_items["explicit_only"].append(
            _make_item(
                kind="explicit_only",
                topic_id=None,
                topic_title=e.title,
                excerpt=f"experiment:{phase_value}:{owner_value}",
                updated_at=e.updated_at,
            )
        )
    for p in todos.pending_advance_rounds:
        bucket_items["explicit_only"].append(
            _make_item(
                kind="explicit_only",
                topic_id=p.topic_id,
                topic_title=p.topic_title,
                excerpt=f"advance_round:{p.discussion_round}",
                updated_at=p.updated_at,
            )
        )
    for pending in todos.pending_replies:
        bucket_items["explicit_only"].append(
            _make_item(
                kind="explicit_only",
                topic_id=pending.experiment_id,
                topic_title=pending.experiment_title,
                excerpt=f"pending_reply:{pending.status.value}",
                updated_at=pending.updated_at,
            )
        )

    # informational_only: read-only review lifecycle (host can dismiss; non-host sees as informational)
    for e in todos.pending_result_reviews:
        bucket_items["informational_only"].append(
            _make_item(
                kind="informational_only",
                topic_id=None,
                topic_title=e.title,
                excerpt=f"result_review:{e.phase.value}",
                updated_at=e.updated_at,
            )
        )
    for informational in todos.experiment_review_informational:
        bucket_items["informational_only"].append(
            _make_item(
                kind="informational_only",
                topic_id=None,
                topic_title=informational.experiment_title,
                excerpt=informational.review_progress,
                updated_at=informational.updated_at,
            )
        )

    # action_items
    for a in todos.action_items:
        bucket_items["action_items"].append(
            _make_item(
                kind="action_items",
                topic_id=a.topic_id,
                topic_title=a.topic_title,
                excerpt=a.title,
                updated_at=a.updated_at,
            )
        )

    # Apply truncation per bucket: keep top-N by updated_at desc.
    truncated_by_bucket: dict[str, int] = {}
    for kind, blist in bucket_items.items():
        blist.sort(key=lambda it: it.updated_at or datetime.min, reverse=True)
        cap = topics_limit if kind != "explicit_only" else experiments_limit
        if len(blist) > cap:
            truncated_by_bucket[kind] = len(blist) - cap
            del blist[cap:]

    # f873c287 I1(e): compute the per-phase_owner experiment counter BEFORE
    # the persona filter so the host can see "3 are mine, 2 are waiting on
    # reviewer" at a glance. The ``host`` key is dropped after the filter
    # below because the underlying bucket is host_only.
    #
    # Counts BOTH explicit_only and informational_only items; both share
    # the ``experiment:{phase}:{owner}[:...]`` excerpt format. We use
    # maxsplit=3 so informational_only entries (which append ``:blocked=``
    # or ``:waiting``) keep the owner as a clean segment.
    experiments_needing_attention_by_owner: dict[str, int] = {}
    for kind in ("explicit_only", "informational_only"):
        for it in bucket_items.get(kind, []):
            if not it.excerpt or not it.excerpt.startswith("experiment:"):
                continue
            parts = it.excerpt.split(":", maxsplit=3)
            if len(parts) < 3:
                continue
            owner = parts[2].strip()
            if not owner:
                continue
            experiments_needing_attention_by_owner[owner] = (
                experiments_needing_attention_by_owner.get(owner, 0) + 1
            )

    # Apply persona filter: non-host loses host_only buckets unless --include-all-personas.
    is_host = _is_host_persona(agent)
    visibility_filter_applied = not (include_all_personas or is_host)
    if visibility_filter_applied:
        bucket_items = {
            k: v for k, v in bucket_items.items() if _BUCKET_KIND_VISIBILITY[k] == "all"
        }
        # f873c287 I1(e): participant/reviewer personas don't see
        # host-owned experiments in the breakdown either — the
        # ``explicit_only`` bucket is host_only.
        experiments_needing_attention_by_owner.pop("host", None)

    buckets = [
        _bucket(kind=kind, items=items_)
        for kind, items_ in bucket_items.items()
    ]

    # Topics needing attention = unique topics that contributed to all / round_ack / pending_reply.
    topics_with_action_items = {
        it.topic_id
        for kind, items_ in bucket_items.items()
        if kind in {"mention", "round_ack", "pending_reply"}
        for it in items_
        if it.topic_id is not None
    }
    topics_needing_attention = len(topics_with_action_items)

    experiments_needing_attention = sum(
        experiments_needing_attention_by_owner.values()
    )

    topics_truncated = sum(
        n for kind, n in truncated_by_bucket.items() if kind != "explicit_only"
    )
    experiments_truncated = sum(
        n for kind, n in truncated_by_bucket.items() if kind == "explicit_only"
    )

    return AgentWorkSummaryRead(
        agent=agent_read,
        buckets=buckets,
        topics_needing_attention=topics_needing_attention,
        experiments_needing_attention=experiments_needing_attention,
        experiments_needing_attention_by_owner=experiments_needing_attention_by_owner,
        topics_truncated=topics_truncated,
        experiments_truncated=experiments_truncated,
        visibility_filter_applied=visibility_filter_applied,
        topics_limit=topics_limit,
        experiments_limit=experiments_limit,
    )
