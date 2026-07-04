"""Per-agent unread topic activity for open discussions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Topic, TopicStatus
from server.domain.schemas import TopicProgressItemRead, TopicProgressListRead
from server.services import topic_work_item_service as work_items


def list_topic_progress_for_agent(db: Session, agent: Agent) -> TopicProgressListRead:
    """List open topics with work items for ``agent`` (unified diff view)."""
    if agent.project_id is None:
        return TopicProgressListRead(items=[], total=0)

    open_topics = list(
        db.scalars(
            select(Topic)
            .where(
                Topic.project_id == agent.project_id,
                Topic.deleted_at.is_(None),
                Topic.archived_at.is_(None),
                Topic.status == TopicStatus.open,
            )
            .order_by(Topic.updated_at.desc())
        )
    )

    all_items = work_items.topic_work_items_for_agent(db, agent)
    items: list[TopicProgressItemRead] = []
    for topic in open_topics:
        topic_items = [i for i in all_items if i.topic_id == topic.id]
        item = work_items.topic_progress_item_from_work_items(db, topic, agent, topic_items)
        if item is not None:
            items.append(item)
    return TopicProgressListRead(items=items, total=len(items))
