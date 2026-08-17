"""M58b-3: DB-direct topic/comment factories for consumer-path tests.

v0.13 M58 retired the DB topic write endpoints (HTTP 410 / CLI exit 2), but
consumer read paths (todos / ack / notifications / progress / action items)
and experiment-domain linkage tests still need DB topic rows as fixtures.
These helpers bypass HTTP: topics are inserted as raw ORM rows; comments go
through the service layer so mention handling and round-ack semantics stay
intact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from map_types.enums import TopicActionItemStatus, TopicStatus
from server.domain.models import Agent, Topic, TopicActionItem, TopicComment, TopicDecision
from server.domain.schemas import TopicCommentCreate
from server.services import topic_comment_service


def db_create_topic(
    db: Session,
    *,
    project_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    title: str = "讨论：测试话题",
    description: str | None = "db-fixture topic (M58b-3)",
    slug: str | None = None,
    status: TopicStatus = TopicStatus.open,
    discussion_round: str = "round1",
    round_summary_count: int = 0,
) -> Topic:
    topic = Topic(
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        title=title,
        description=description,
        slug=slug,
        status=status,
        discussion_round=discussion_round,
        round_summary_count=round_summary_count,
    )
    db.add(topic)
    db.flush()
    return topic


def db_add_comment(
    db: Session,
    *,
    topic_id: uuid.UUID,
    author: Agent,
    body: str,
    parent_id: uuid.UUID | None = None,
    is_round_summary: bool = False,
    kind: str | None = None,
) -> TopicComment:
    create = TopicCommentCreate(
        body=body,
        parent_id=parent_id,
        is_round_summary=is_round_summary,
    )
    if kind is not None:
        create = create.model_copy(update={"kind": kind})
    comment, _ = topic_comment_service.create_topic_comment(
        db,
        topic_id,
        author,
        create,
        commit=False,
    )
    # SQLite stores naive datetimes; the service writes tz-aware values onto
    # the in-memory topic row (updated_at). Expire so later comparisons in
    # todo/work-item services read consistent naive values from the DB.
    db.expire_all()
    return comment


def db_resolve_with_action_items(
    db: Session,
    topic: Topic,
    *,
    author: Agent,
    action_items: list[dict],
    decision: str = "d",
) -> list[TopicActionItem]:
    """Direct-insert a decision + action items (replaces retired POST /resolve).

    ``action_items`` entries accept title / description / owner_agent_id /
    category / id / status — the same shape the resolve payload used, so
    consumer tests (complete / cancel / audit) keep their fixtures intact.
    """
    decision_row = TopicDecision(
        project_id=topic.project_id,
        topic_id=topic.id,
        author_agent_id=author.id,
        decision=decision,
    )
    db.add(decision_row)
    db.flush()
    rows = []
    for spec in action_items:
        owner = spec.get("owner_agent_id")
        if isinstance(owner, str):
            owner = uuid.UUID(owner)
        linked = spec.get("linked_experiment_id")
        if isinstance(linked, str):
            linked = uuid.UUID(linked)
        row = TopicActionItem(
            decision_id=decision_row.id,
            project_id=topic.project_id,
            topic_id=topic.id,
            title=spec["title"],
            description=spec.get("description"),
            owner_agent_id=owner,
            linked_experiment_id=linked,
            category=spec.get("category"),
            status=spec.get("status", TopicActionItemStatus.open),
            # I1/I3 契约：service 层在创建行时打 first_open_at；直插路径补齐
            first_open_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        if spec.get("id") is not None:
            row.id = spec["id"] if isinstance(spec["id"], uuid.UUID) else uuid.UUID(spec["id"])
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def db_topic_json(
    db: Session,
    *,
    project_id,
    creator_agent_id,
    title: str = "db-fixture topic",
    description: str | None = None,
) -> dict:
    """DB-insert a topic and return the JSON shape tests used to get from POST /topics."""
    if not isinstance(project_id, uuid.UUID):
        project_id = uuid.UUID(project_id)
    if not isinstance(creator_agent_id, uuid.UUID):
        creator_agent_id = uuid.UUID(creator_agent_id)
    topic = db_create_topic(
        db, project_id=project_id, creator_agent_id=creator_agent_id,
        title=title, description=description,
    )
    return {
        "id": str(topic.id),
        "title": topic.title,
        "creator_agent_id": str(topic.creator_agent_id),
    }


def db_resolve_item_json(
    db: Session,
    topic: Topic,
    *,
    author: Agent,
    owner_agent_id,
    title: str,
) -> dict:
    """DB-insert a one-item decision and return ``{"id": ...}`` JSON shape."""
    row = db_resolve_with_action_items(
        db, topic, author=author,
        action_items=[{"title": title, "owner_agent_id": owner_agent_id}],
    )[0]
    return {"id": str(row.id), "title": title}
