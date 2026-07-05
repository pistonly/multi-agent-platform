import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from map_types.enums import AgentRole, ExperimentPhase, TopicActionItemStatus, TopicDiscussionRound
from map_types.schemas import ActionItemCancel
from server.domain.models import Agent, Experiment, Topic, TopicActionItem, TopicComment, TopicDecision, TopicReadCursor, TopicStatus
from server.domain.schemas import (
    ExperimentSummaryRead,
    TopicActionItemRead,
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
    TopicCreate,
    TopicDecisionRead,
    TopicRead,
    TopicReadCursorRead,
    TopicResolve,
    TopicSummaryRead,
    TopicUpdate,
)
from server.services import action_item_service, audit_service, notification_service, topic_ack_service
from server.services.errors import ConflictError, ForbiddenError, NotFoundError, StateTransitionError
from server.services.permissions import is_admin
from server.services.project_service import get_project
from server.services.thread_activity import topic_comment_order_clauses, topic_comment_order_clauses_desc


def _get_topic(db: Session, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or topic.deleted_at is not None:
        raise NotFoundError("Topic not found")
    return topic


def _agent_names_by_ids(db: Session, agent_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not agent_ids:
        return {}
    return {
        agent.id: agent.name
        for agent in db.scalars(select(Agent).where(Agent.id.in_(agent_ids)))
    }


def topic_summary(db: Session, topic: Topic, *, viewer_agent_id: uuid.UUID | None = None) -> TopicSummaryRead:
    return topic_summaries_for_topics(db, [topic], viewer_agent_id=viewer_agent_id)[0]


def dismiss_topic(db: Session, *, agent: Agent, topic_id: uuid.UUID) -> Topic | None:
    """Host-only: hide an open topic from the creator's /todos.

    Re-surfaces automatically when the topic receives new activity
    (`updated_at` is bumped on new comments), so the dismiss is a soft
    "I've seen the current state" rather than a permanent archive.
    """
    topic = db.get(Topic, topic_id)
    if topic is None or topic.creator_agent_id != agent.id:
        return None
    if topic.dismissed_at is None:
        topic.dismissed_at = datetime.now(UTC)
        topic.dismissed_by_agent_id = agent.id
        db.commit()
        db.refresh(topic)
    return topic


def mark_topic_read(
    db: Session,
    *,
    agent: Agent,
    topic_id: uuid.UUID,
) -> TopicReadCursorRead:
    """Advance per-agent read cursor to the latest comment_seq (T3 D4).

    Independent transaction from comment INSERT (T3-1). Does not clear
    obligation work items (reply / mention / round_ack).
    """
    from server.services.permissions import ensure_topic_access

    topic = ensure_topic_access(db, agent, topic_id)
    max_seq = int(
        db.scalar(
            select(func.coalesce(func.max(TopicComment.comment_seq), 0)).where(
                TopicComment.topic_id == topic.id
            )
        )
        or 0
    )
    now = datetime.now(UTC)
    cursor = db.scalar(
        select(TopicReadCursor).where(
            TopicReadCursor.topic_id == topic.id,
            TopicReadCursor.agent_id == agent.id,
        )
    )
    if cursor is None:
        cursor = TopicReadCursor(
            topic_id=topic.id,
            agent_id=agent.id,
            last_read_comment_seq=max_seq,
        )
        db.add(cursor)
    else:
        cursor.last_read_comment_seq = max_seq
        cursor.updated_at = now
    db.commit()
    db.refresh(cursor)
    return TopicReadCursorRead(
        topic_id=cursor.topic_id,
        agent_id=cursor.agent_id,
        last_read_comment_seq=cursor.last_read_comment_seq,
        updated_at=cursor.updated_at,
    )


def topic_summaries_for_topics(
    db: Session,
    topics: list[Topic],
    *,
    viewer_agent_id: uuid.UUID | None = None,
) -> list[TopicSummaryRead]:
    if not topics:
        return []

    topic_ids = [topic.id for topic in topics]
    comment_counts = {
        topic_id: count
        for topic_id, count in db.execute(
            select(TopicComment.topic_id, func.count())
            .where(TopicComment.topic_id.in_(topic_ids))
            .group_by(TopicComment.topic_id)
        )
    }
    experiment_counts = {
        topic_id: count
        for topic_id, count in db.execute(
            select(Experiment.topic_id, func.count())
            .where(
                Experiment.topic_id.in_(topic_ids),
                Experiment.deleted_at.is_(None),
                Experiment.phase != ExperimentPhase.cancelled,
            )
            .group_by(Experiment.topic_id)
        )
    }
    creator_names = _agent_names_by_ids(db, {topic.creator_agent_id for topic in topics})
    latest_comments = _latest_topic_comments_by_topic(db, topic_ids)
    latest_author_names = _agent_names_by_ids(
        db, {comment.author_agent_id for comment in latest_comments.values()}
    )
    my_comment_counts: dict[uuid.UUID, int] = {}
    if viewer_agent_id is not None:
        my_comment_counts = _my_comment_counts_by_topic(db, topic_ids, viewer_agent_id)
    return [
        TopicSummaryRead(
            id=topic.id,
            project_id=topic.project_id,
            creator_agent_id=topic.creator_agent_id,
            creator_name=creator_names.get(topic.creator_agent_id),
            title=topic.title,
            description=topic.description,
            status=topic.status,
            pinned=topic.pinned,
            discussion_round=topic.discussion_round,
            round_summary_count=topic.round_summary_count,
            comment_count=comment_counts.get(topic.id, 0),
            experiment_count=experiment_counts.get(topic.id, 0),
            last_comment_id=latest_comments[topic.id].id if topic.id in latest_comments else None,
            last_comment_author_agent_id=(
                latest_comments[topic.id].author_agent_id if topic.id in latest_comments else None
            ),
            last_comment_author_name=(
                latest_author_names.get(latest_comments[topic.id].author_agent_id)
                if topic.id in latest_comments
                else None
            ),
            last_comment_excerpt=(
                _topic_comment_excerpt(latest_comments[topic.id].body)
                if topic.id in latest_comments
                else None
            ),
            my_comment_count=my_comment_counts.get(topic.id, 0) if viewer_agent_id is not None else None,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
            archived_at=topic.archived_at,
            dismissed_at=topic.dismissed_at,
            advance_round_pending_since=topic.advance_round_pending_since,
        )
        for topic in topics
    ]


def _topic_comment_excerpt(body: str) -> str:
    text = body.strip().replace("\n", " ")
    limit = 200
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _latest_topic_comments_by_topic(
    db: Session, topic_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TopicComment]:
    if not topic_ids:
        return {}
    latest: dict[uuid.UUID, TopicComment] = {}
    for topic_id in topic_ids:
        comment = db.scalars(
            select(TopicComment)
            .where(TopicComment.topic_id == topic_id)
            .order_by(*topic_comment_order_clauses_desc())
            .limit(1)
        ).first()
        if comment is not None:
            latest[topic_id] = comment
    return latest


def _my_comment_counts_by_topic(
    db: Session, topic_ids: list[uuid.UUID], agent_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    if not topic_ids:
        return {}
    return {
        topic_id: count
        for topic_id, count in db.execute(
            select(TopicComment.topic_id, func.count())
            .where(
                TopicComment.topic_id.in_(topic_ids),
                TopicComment.author_agent_id == agent_id,
            )
            .group_by(TopicComment.topic_id)
        )
    }


def _latest_comment_authors_by_topic(
    db: Session, topic_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    return {
        topic_id: comment.author_agent_id
        for topic_id, comment in _latest_topic_comments_by_topic(db, topic_ids).items()
    }


def _suggest_linked_experiment(db: Session, item: TopicActionItem) -> tuple[uuid.UUID | None, str | None]:
    """Find a recently-done experiment in the same project whose owner matches
    the action item's owner and whose title is a substring match (case-insensitive,
    space-insensitive) of the action item title. Returns ``(None, None)`` when
    the item is not eligible (closed, already linked, no owner) or no match.
    """
    if item.status != TopicActionItemStatus.open:
        return None, None
    if item.linked_experiment_id is not None:
        return None, None
    if item.owner_agent_id is None:
        return None, None

    cutoff = datetime.now(UTC) - timedelta(days=30)
    item_norm = item.title.lower().replace(" ", "")
    candidates = db.scalars(
        select(Experiment)
        .where(
            Experiment.project_id == item.project_id,
            Experiment.creator_agent_id == item.owner_agent_id,
            Experiment.phase == ExperimentPhase.done,
            Experiment.deleted_at.is_(None),
            Experiment.archived_at.is_(None),
            Experiment.updated_at >= cutoff,
        )
        .order_by(Experiment.updated_at.desc())
        .limit(50)
    ).all()
    for exp in candidates:
        exp_norm = exp.title.lower().replace(" ", "")
        if not exp_norm or not item_norm:
            continue
        if exp_norm in item_norm or item_norm in exp_norm:
            return exp.id, exp.title
    return None, None


def _action_item_read(db: Session, item: TopicActionItem) -> TopicActionItemRead:
    owner_name = None
    if item.owner_agent_id is not None:
        owner = getattr(item, "owner", None)
        if owner is None:
            owner = db.get(Agent, item.owner_agent_id)
        owner_name = owner.name if owner else None
    suggested_id, suggested_title = _suggest_linked_experiment(db, item)
    return TopicActionItemRead(
        id=item.id,
        decision_id=item.decision_id,
        project_id=item.project_id,
        topic_id=item.topic_id,
        title=item.title,
        description=item.description,
        owner_agent_id=item.owner_agent_id,
        owner_name=owner_name,
        status=item.status,
        due_at=item.due_at,
        linked_experiment_id=item.linked_experiment_id,
        category=item.category,
        cancel_reason=item.cancel_reason,
        suggested_linked_experiment_id=suggested_id,
        suggested_linked_experiment_title=suggested_title,
        wake_count=item.wake_count,
        first_open_at=item.first_open_at,
        last_woken_at=item.last_woken_at,
        stale_at=item.stale_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def topic_decision_read(db: Session, decision: TopicDecision) -> TopicDecisionRead:
    author_name = None
    author = getattr(decision, "author", None)
    if author is None:
        author = db.get(Agent, decision.author_agent_id)
    if author is not None:
        author_name = author.name

    topic_title = None
    topic = getattr(decision, "topic", None)
    if topic is None:
        topic = db.get(Topic, decision.topic_id)
    if topic is not None:
        topic_title = topic.title

    return TopicDecisionRead(
        id=decision.id,
        project_id=decision.project_id,
        topic_id=decision.topic_id,
        topic_title=topic_title,
        author_agent_id=decision.author_agent_id,
        author_name=author_name,
        decision=decision.decision,
        rationale=decision.rationale,
        rejected_options=decision.rejected_options,
        open_questions=decision.open_questions,
        no_decision_reason=decision.no_decision_reason,
        action_items=[_action_item_read(db, item) for item in decision.action_items],
        created_at=decision.created_at,
        updated_at=decision.updated_at,
    )


def _load_decision(db: Session, topic_id: uuid.UUID) -> TopicDecision | None:
    stmt = (
        select(TopicDecision)
        .where(TopicDecision.topic_id == topic_id)
        .options(
            joinedload(TopicDecision.author),
            joinedload(TopicDecision.topic),
            joinedload(TopicDecision.action_items).joinedload(TopicActionItem.owner),
        )
    )
    return db.execute(stmt).unique().scalar_one_or_none()


def create_topic(
    db: Session,
    project_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    payload: TopicCreate,
) -> Topic:
    get_project(db, project_id)
    topic = Topic(
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        title=payload.title,
        description=payload.description,
        status=TopicStatus.open,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def list_topics(
    db: Session,
    project_id: uuid.UUID,
    *,
    status: TopicStatus | None = None,
    creator_agent_id: uuid.UUID | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 100,
    include_archived: bool = False,
    viewer_agent_id: uuid.UUID | None = None,
) -> tuple[list[TopicSummaryRead], int]:
    get_project(db, project_id)
    stmt = select(Topic).where(Topic.project_id == project_id, Topic.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Topic.archived_at.is_(None))
    if status is not None:
        stmt = stmt.where(Topic.status == status)
    if creator_agent_id is not None:
        stmt = stmt.where(Topic.creator_agent_id == creator_agent_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Topic.title.ilike(pattern) | Topic.description.ilike(pattern))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    stmt = stmt.order_by(Topic.pinned.desc(), Topic.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    topics = list(db.scalars(stmt))
    return topic_summaries_for_topics(db, topics, viewer_agent_id=viewer_agent_id), total


def get_topic_detail(db: Session, topic_id: uuid.UUID) -> TopicRead:
    topic = _get_topic(db, topic_id)
    summary = topic_summary(db, topic)

    exp_stmt = (
        select(Experiment)
        .where(
            Experiment.topic_id == topic.id,
            Experiment.deleted_at.is_(None),
            Experiment.phase != ExperimentPhase.cancelled,
        )
        .order_by(Experiment.created_at.desc())
    )
    experiments = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(exp_stmt)]
    comments = list(db.scalars(
        select(TopicComment)
        .where(TopicComment.topic_id == topic.id)
        .order_by(*topic_comment_order_clauses())
    ))
    comments_tree = _build_comment_tree(comments, _agent_names_by_ids(
        db, {comment.author_agent_id for comment in comments}
    ))

    decision = _load_decision(db, topic.id)
    return TopicRead(
        **summary.model_dump(),
        experiments=experiments,
        comments=comments_tree,
        decision=topic_decision_read(db, decision) if decision is not None else None,
    )


def resolve_topic(
    db: Session,
    topic_id: uuid.UUID,
    author: Agent,
    payload: TopicResolve,
) -> TopicDecision:
    topic = _get_topic(db, topic_id)
    decision = db.scalar(select(TopicDecision).where(TopicDecision.topic_id == topic_id))
    if decision is None:
        decision = TopicDecision(
            project_id=topic.project_id,
            topic_id=topic.id,
            author_agent_id=author.id,
        )
        db.add(decision)
        db.flush()
    else:
        decision.author_agent_id = author.id

    decision.decision = payload.decision
    decision.rationale = payload.rationale
    decision.rejected_options = payload.rejected_options
    decision.open_questions = payload.open_questions
    decision.no_decision_reason = payload.no_decision_reason

    # --- 二次约束: 旧项不在新 payload 中 → 等价显式 done (A1 resolve idempotency) -
    # Up until v0.x the resolver did ``db.execute(delete(TopicActionItem))`` and
    # re-inserted from payload, which made every re-resolve a destructive
    # delete-and-rebuild and made ``status=done`` impossible to persist. The new
    # rule is: items not present in the new payload (by id) are auto-closed as
    # ``done``; items present are upserted by id (fields updated, status
    # preserved); items with no id are inserted as open.
    existing = list(
        db.scalars(select(TopicActionItem).where(TopicActionItem.decision_id == decision.id))
    )
    existing_by_id = {item.id: item for item in existing}
    new_payload_ids: set[uuid.UUID] = {
        item.id for item in payload.action_items if item.id is not None
    }

    audit_entries: list[dict[str, object]] = []
    for old_item in existing:
        if old_item.id in new_payload_ids:
            continue
        # 旧项不在新 payload：若仍为 open，等价显式 done（写 audit；已 closed 项保持原状）。
        if old_item.status == TopicActionItemStatus.open:
            prev_status = old_item.status
            old_item.status = TopicActionItemStatus.done
            old_item.updated_at = datetime.now(UTC)
            audit_entries.append(
                {
                    "action": "action_item.completed",
                    "target_id": old_item.id,
                    "payload": {
                        "action_item_id": str(old_item.id),
                        "prev_status": prev_status.value,
                        "new_status": old_item.status.value,
                        "triggered_by": "topic_resolve",
                    },
                }
            )

    for item_payload in payload.action_items:
        if item_payload.owner_agent_id is not None:
            owner = db.get(Agent, item_payload.owner_agent_id)
            if owner is None or (owner.project_id != topic.project_id and owner.role != AgentRole.admin):
                raise NotFoundError("Action item owner agent not found")
        if item_payload.linked_experiment_id is not None:
            experiment = db.get(Experiment, item_payload.linked_experiment_id)
            if experiment is None or experiment.deleted_at is not None or experiment.project_id != topic.project_id:
                raise NotFoundError("Linked experiment not found")
        if item_payload.id is not None and item_payload.id in existing_by_id:
            old_item = existing_by_id[item_payload.id]
            old_item.title = item_payload.title
            old_item.description = item_payload.description
            old_item.owner_agent_id = item_payload.owner_agent_id
            old_item.due_at = item_payload.due_at
            old_item.linked_experiment_id = item_payload.linked_experiment_id
            if item_payload.category is not None:
                old_item.category = item_payload.category.value
        else:
            # Stamp first_open_at at creation so the waker's T+24h / T+72h
            # escalation timer starts immediately on resolve. Plan §2: open
            # transitions are the moment the timer anchors to. Existing open
            # items are backfilled by alembic migration 027.
            db.add(
                TopicActionItem(
                    decision_id=decision.id,
                    project_id=topic.project_id,
                    topic_id=topic.id,
                    title=item_payload.title,
                    description=item_payload.description,
                    owner_agent_id=item_payload.owner_agent_id,
                    status=TopicActionItemStatus.open,
                    due_at=item_payload.due_at,
                    linked_experiment_id=item_payload.linked_experiment_id,
                    category=item_payload.category.value if item_payload.category else None,
                    first_open_at=datetime.now(UTC),
                )
            )

    db.commit()

    # Audit logs are written post-commit so a half-failed resolve cannot leak
    # events. ``audit_service.log`` opens its own transaction; if it fails the
    # decision is already persisted.
    for entry in audit_entries:
        audit_service.log(
            db,
            action=entry["action"],
            target_type="topic_action_item",
            target_id=entry["target_id"],
            agent_id=author.id,
            project_id=topic.project_id,
            summary="resolve 二次约束关闭 action_item",
            payload=entry["payload"],
        )

    loaded = _load_decision(db, topic.id)
    if loaded is None:
        raise NotFoundError("Topic decision not found")
    return loaded


def list_project_decisions(
    db: Session,
    project_id: uuid.UUID,
    *,
    limit: int = 20,
) -> list[TopicDecisionRead]:
    get_project(db, project_id)
    rows = list(
        db.scalars(
            select(TopicDecision)
            .where(TopicDecision.project_id == project_id)
            .options(
                joinedload(TopicDecision.author),
                joinedload(TopicDecision.topic),
                joinedload(TopicDecision.action_items).joinedload(TopicActionItem.owner),
            )
            .order_by(TopicDecision.updated_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        .unique()
    )
    return [topic_decision_read(db, row) for row in rows]


def list_action_items(
    db: Session,
    project_id: uuid.UUID,
    *,
    owner_agent_id: uuid.UUID | None = None,
    status: TopicActionItemStatus | None = None,
    limit: int = 100,
) -> list[TopicActionItemRead]:
    get_project(db, project_id)
    stmt = (
        select(TopicActionItem)
        .where(TopicActionItem.project_id == project_id)
        .options(joinedload(TopicActionItem.owner))
        .order_by(TopicActionItem.updated_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if owner_agent_id is not None:
        stmt = stmt.where(TopicActionItem.owner_agent_id == owner_agent_id)
    if status is not None:
        stmt = stmt.where(TopicActionItem.status == status)
    return [_action_item_read(db, item) for item in db.scalars(stmt)]


def _ensure_action_item_accessor(item: TopicActionItem, agent: Agent) -> None:
    """Caller must be the owner or an admin. Topic creator may be added later."""
    if item.owner_agent_id is None:
        # No owner assigned: admin-only is the safe default.
        if not is_admin(agent):
            raise ForbiddenError("Only admin can close an unassigned action item")
        return
    if item.owner_agent_id != agent.id and not is_admin(agent):
        raise ForbiddenError("Only the owner or admin can complete/cancel this action item")


def _complete_action_item_no_commit(
    db: Session,
    item: TopicActionItem,
    *,
    triggered_by: str = "manual",
) -> dict:
    """Move an open action item to ``done`` within the caller's transaction.

    Caller is responsible for:
      - 404 lookup (db.get on TopicActionItem)
      - Owner / admin access check (``_ensure_action_item_accessor``)
      - Final ``db.commit()``

    Returns a dict the caller can splice into a higher-level audit payload
    (e.g. ``cascaded_action_items`` on ``experiment.completed``).
    """
    if item.status != TopicActionItemStatus.open:
        raise ConflictError(f"Action item already {item.status.value}")
    prev_status = item.status
    item.status = TopicActionItemStatus.done
    audit_service._log_no_commit(
        db,
        action="action_item.completed",
        target_type="topic_action_item",
        target_id=item.id,
        project_id=item.project_id,
        summary=f"完成行动项「{item.title}」",
        payload={
            "action_item_id": str(item.id),
            "prev_status": prev_status.value,
            "new_status": item.status.value,
            "triggered_by": triggered_by,
        },
    )
    return {
        "action_item_id": str(item.id),
        "prev_status": prev_status.value,
        "new_status": item.status.value,
    }


def complete_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
    *,
    triggered_by: str = "manual",
) -> TopicActionItemRead:
    """Move an open action item to ``done``. Idempotent against non-open statuses (409)."""
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    _complete_action_item_no_commit(db, item, triggered_by=triggered_by)
    db.commit()
    db.refresh(item)
    return _action_item_read(db, item)


def mark_wake_sent_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
) -> TopicActionItemRead:
    """Bump wake_count + stamp last_woken_at + audit row (experiment B, plan §3).

    Access: owner or admin — same gate as ``complete`` / ``cancel`` so the
    runtime-waker CLI (driven by an admin agent) can escalate forgotten
    action_items whose owner is unresponsive. The transaction-internal
    helper does the mutation; this wrapper adds the access check + commit
    + read-back serialization.

    I5: also posts a wakeable notification to the assignee so the bump
    shows up in the owner's todos (B-1 / B-2 acceptance — wake is visible
    to the assignee independent of the audit log).
    """
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    try:
        action_item_service.mark_wake_sent(db, action_item_id)
    except ValueError as exc:
        # The I3 helper raises ValueError on non-open / unassigned items —
        # surface as ConflictError so the API returns 409 instead of 500.
        raise ConflictError(str(exc)) from exc
    # I5: notify the assignee. ``mark_wake_sent`` refreshes the identity-mapped
    # item in place, so ``item`` here reflects the new wake_count + last_woken_at
    # when we hand it to the notification helper.
    notification_service.notify_owner_action_item_wake(db, action_item=item)
    return _action_item_read(db, item)


def mark_stale_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
) -> TopicActionItemRead:
    """Stamp stale_at + write the ``action_item.stale`` audit row (plan §3).

    Access: admin only — the stale transition is a system escalation that
    should not be triggerable by the assignee themselves, since the whole
    point is that they have not responded to 4 wakes. ``admin_notified=True``
    and ``creator_audit_only=True`` are passed per plan §3 §6 (3c admin 优先
    + 6 creator 不 wake).

    I5: also posts a wakeable notification to every admin agent (excluding
    the owner) — plan §3 #2 (3c admin 优先). The creator is intentionally
    NOT in the recipient set (I6 audit-only policy): they can find the
    stale event via ``action_items`` listing or the audit history.
    """
    if not is_admin(agent):
        raise ForbiddenError("Only admin can mark an action item stale")
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    try:
        action_item_service.mark_stale(db, action_item_id)
    except ValueError as exc:
        # The audit-service helper refuses stale without a prior wake, and
        # refuses non-open items — both surface as ConflictError (409) so
        # the waker CLI can distinguish "retry later" from "5xx bug".
        raise ConflictError(str(exc)) from exc
    # I5: fan out to admin agents. No-op when the project has no admin
    # configured (per plan risk #3 this is a degraded state — the audit
    # row is still written, but no notification reaches a human).
    notification_service.notify_admin_action_item_stale(db, action_item=item)
    return _action_item_read(db, item)


def cancel_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    agent: Agent,
    payload: ActionItemCancel,
    *,
    triggered_by: str = "manual",
) -> TopicActionItemRead:
    """Move an open action item to ``cancelled``. Reason + category validated by schema."""
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    if item.status != TopicActionItemStatus.open:
        raise ConflictError(f"Action item already {item.status.value}")
    prev_status = item.status
    item.status = TopicActionItemStatus.cancelled
    item.cancel_reason = payload.reason
    if payload.category is not None:
        item.category = payload.category.value
    db.commit()
    db.refresh(item)
    audit_service.log(
        db,
        action="action_item.cancelled",
        target_type="topic_action_item",
        target_id=item.id,
        agent_id=agent.id,
        project_id=item.project_id,
        summary=f"取消行动项「{item.title}」",
        payload={
            "action_item_id": str(item.id),
            "prev_status": prev_status.value,
            "new_status": item.status.value,
            "cancel_reason": payload.reason,
            "category": (payload.category.value if payload.category else item.category),
            "triggered_by": triggered_by,
        },
    )
    return _action_item_read(db, item)


def link_action_item(
    db: Session,
    action_item_id: uuid.UUID,
    experiment_id: uuid.UUID,
    agent: Agent,
) -> TopicActionItemRead:
    """Attach an experiment to an open action item (``linked_experiment_id``).

    Idempotency: re-linking the same experiment is a no-op (returns the item).
    Relinking to a *different* experiment is rejected with 409 — to change link
    target, first unlink via service-internal flow (not currently exposed).

    Permissions: owner or admin (same as ``complete`` / ``cancel``).
    """
    from server.services.project_service import get_experiment

    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise NotFoundError("Action item not found")
    _ensure_action_item_accessor(item, agent)
    if item.status != TopicActionItemStatus.open:
        raise ConflictError(f"Cannot link a {item.status.value} action item")

    experiment = get_experiment(db, experiment_id)
    if experiment.project_id != item.project_id:
        raise ConflictError("Action item and experiment belong to different projects")
    allowed_phases = {
        ExperimentPhase.running,
        ExperimentPhase.result_review,
        ExperimentPhase.done,
    }
    if experiment.phase not in allowed_phases:
        raise ConflictError(
            f"Cannot link experiment in phase {experiment.phase.value}; "
            "must be running, result_review, or done"
        )

    if item.linked_experiment_id == experiment_id:
        return _action_item_read(db, item)
    if item.linked_experiment_id is not None:
        raise ConflictError(
            "Action item is already linked to a different experiment; "
            "unlink first (not yet supported via CLI)"
        )

    item.linked_experiment_id = experiment_id
    db.commit()
    db.refresh(item)
    audit_service.log(
        db,
        action="action_item.linked",
        target_type="topic_action_item",
        target_id=item.id,
        agent_id=agent.id,
        project_id=item.project_id,
        summary=f"关联行动项「{item.title}」到实验「{experiment.title}」",
        payload={
            "action_item_id": str(item.id),
            "experiment_id": str(experiment_id),
            "triggered_by": "manual",
        },
    )
    return _action_item_read(db, item)


def update_topic(db: Session, topic_id: uuid.UUID, payload: TopicUpdate) -> Topic:
    topic = _get_topic(db, topic_id)
    data = payload.model_dump(exclude_unset=True)
    archived = data.pop("archived", None)
    for key, value in data.items():
        setattr(topic, key, value)
    if archived is not None:
        topic.archived_at = datetime.now(UTC) if archived else None
    db.commit()
    db.refresh(topic)
    return topic


def soft_delete_topic(db: Session, topic_id: uuid.UUID) -> None:
    topic = _get_topic(db, topic_id)
    topic.deleted_at = datetime.now(UTC)
    db.commit()


def set_topic_status(db: Session, topic_id: uuid.UUID, target: TopicStatus) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status == target:
        return topic
    valid = (
        (target == TopicStatus.closed and topic.status == TopicStatus.open)
        or (target == TopicStatus.open and topic.status == TopicStatus.closed)
    )
    if not valid:
        raise StateTransitionError(f"Topic cannot move from {topic.status.value} to {target.value}")
    topic.status = target
    db.commit()
    db.refresh(topic)
    return topic


def advance_topic_round(
    db: Session,
    topic_id: uuid.UUID,
    *,
    increment_summary: bool = True,
    acknowledged_by: list[uuid.UUID] | None = None,
) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status != TopicStatus.open:
        raise ConflictError("Cannot advance a closed topic")
    if topic.discussion_round == TopicDiscussionRound.ready:
        raise ConflictError("Topic discussion round is already ready")

    topic_ack_service.validate_advance_ack(
        db,
        topic,
        acknowledged_by=acknowledged_by or [],
    )

    current_count = topic.round_summary_count or 0
    next_count = current_count + 1 if increment_summary else current_count

    if topic.discussion_round == TopicDiscussionRound.round1:
        if next_count < 1:
            raise ConflictError("Cannot advance round1 before at least one round summary")
        topic.discussion_round = TopicDiscussionRound.round2
    elif topic.discussion_round == TopicDiscussionRound.round2:
        if next_count < 2:
            raise ConflictError("Cannot advance round2 before two round summaries")
        topic.discussion_round = TopicDiscussionRound.ready
    else:
        raise ConflictError(f"Unknown topic discussion round: {topic.discussion_round}")

    topic.round_summary_count = next_count
    topic.advance_round_pending_since = None
    db.commit()
    db.refresh(topic)
    return topic


def record_participant_round_ack(
    db: Session,
    topic_id: uuid.UUID,
    agent: Agent,
    kind: str,
) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status != TopicStatus.open:
        raise ConflictError("Cannot ack a closed topic")
    if topic.archived_at is not None:
        raise ConflictError("Cannot ack an archived topic")
    if agent.id == topic.creator_agent_id:
        raise ConflictError("Host cannot post participant ack; use acknowledged_by when advancing")

    create_topic_comment(
        db,
        topic_id,
        agent,
        TopicCommentCreate(body=topic_ack_service.participant_ack_body(kind)),
    )
    db.refresh(topic)
    return topic


def _build_comment_tree(
    comments: list[TopicComment],
    author_names: dict[uuid.UUID, str],
) -> list[TopicCommentTreeNode]:
    _ensure_comment_seq_values(comments)
    nodes: dict[uuid.UUID, TopicCommentTreeNode] = {}
    for comment in comments:
        nodes[comment.id] = TopicCommentTreeNode(
            id=comment.id,
            topic_id=comment.topic_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
            parent_comment_id=comment.parent_comment_id,
            body=comment.body,
            comment_seq=comment.comment_seq,
            created_at=comment.created_at,
            children=[],
        )
    roots: list[TopicCommentTreeNode] = []
    for comment in comments:
        node = nodes[comment.id]
        if comment.parent_comment_id and comment.parent_comment_id in nodes:
            nodes[comment.parent_comment_id].children.append(node)
        else:
            roots.append(node)
    return roots


def _ensure_comment_seq_values(comments: list[TopicComment]) -> None:
    if all(comment.comment_seq is not None for comment in comments):
        return
    by_topic: dict[uuid.UUID, list[TopicComment]] = {}
    for comment in comments:
        by_topic.setdefault(comment.topic_id, []).append(comment)
    for topic_comments in by_topic.values():
        ordered = sorted(
            topic_comments,
            key=lambda comment: (
                comment.created_at,
                comment.comment_seq if comment.comment_seq is not None else 0,
                comment.id,
            ),
        )
        for index, comment in enumerate(ordered, start=1):
            if comment.comment_seq is None:
                comment.comment_seq = index


def _next_topic_comment_seq(db: Session, topic_id: uuid.UUID) -> int:
    current = db.scalar(
        select(func.coalesce(func.max(TopicComment.comment_seq), 0)).where(
            TopicComment.topic_id == topic_id
        )
    )
    return int(current or 0) + 1


def create_topic_comment(
    db: Session,
    topic_id: uuid.UUID,
    author: Agent,
    payload: TopicCommentCreate,
) -> tuple[TopicComment, list[str]]:
    topic = _get_topic(db, topic_id)
    if payload.parent_id is not None:
        parent = db.scalar(
            select(TopicComment).where(
                TopicComment.id == payload.parent_id, TopicComment.topic_id == topic_id
            )
        )
        if parent is None:
            raise NotFoundError("Parent comment not found")
    comment = TopicComment(
        topic_id=topic_id,
        author_agent_id=author.id,
        parent_comment_id=payload.parent_id,
        body=payload.body,
        comment_seq=_next_topic_comment_seq(db, topic_id),
    )
    db.add(comment)
    # Bump topic.updated_at so any prior host-side dismiss on this topic
    # is automatically un-dismissed — there's new activity worth seeing.
    topic.updated_at = datetime.now(UTC)
    if (
        author.id == topic.creator_agent_id
        and payload.parent_id is None
        and topic_ack_service.is_round_summary_comment(payload.body)
    ):
        topic_ack_service.mark_round_ack_pending(topic)
    db.flush()
    db.refresh(comment)

    from server.services import mention_service

    unresolved = mention_service.process_topic_comment_mentions(
        db, comment=comment, author=author, topic=topic, commit=False
    )
    mention_service.auto_dismiss_mentions_after_comment(
        db,
        new_comment_author=author,
        experiment_id=None,
        topic_id=topic_id,
        new_comment_id=comment.id,
        commit=False,
    )
    db.commit()
    return comment, unresolved


def topic_comment_read(
    db: Session, comment: TopicComment, *, unresolved_mentions: list[str] | None = None
) -> TopicCommentRead:
    author_names = _agent_names_by_ids(db, {comment.author_agent_id})
    _ensure_comment_seq_values([comment])
    return TopicCommentRead(
        id=comment.id,
        topic_id=comment.topic_id,
        author_agent_id=comment.author_agent_id,
        author_name=author_names.get(comment.author_agent_id),
        parent_comment_id=comment.parent_comment_id,
        body=comment.body,
        comment_seq=comment.comment_seq,
        created_at=comment.created_at,
        unresolved_mentions=list(unresolved_mentions or ()),
    )


def list_topic_comments(
    db: Session,
    topic_id: uuid.UUID,
    *,
    tree: bool = False,
) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
    _get_topic(db, topic_id)
    stmt = (
        select(TopicComment)
        .where(TopicComment.topic_id == topic_id)
        .order_by(*topic_comment_order_clauses())
    )
    comments = list(db.scalars(stmt))
    _ensure_comment_seq_values(comments)
    author_names = _agent_names_by_ids(db, {comment.author_agent_id for comment in comments})
    if tree:
        return _build_comment_tree(comments, author_names)
    return [
        TopicCommentRead(
            id=comment.id,
            topic_id=comment.topic_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
            parent_comment_id=comment.parent_comment_id,
            body=comment.body,
            comment_seq=comment.comment_seq,
            created_at=comment.created_at,
        )
        for comment in comments
    ]
