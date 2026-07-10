"""Per-agent unread topic activity for open discussions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from server.domain.models import Agent
from server.domain.schemas import TopicProgressItemRead, TopicProgressListRead
from server.services import topic_work_item_service as work_items


def list_topic_progress_for_agent(
    db: Session,
    agent: Agent,
    *,
    bundle: work_items.AgentTopicWorkItems | None = None,
) -> TopicProgressListRead:
    """List open topics with work items for ``agent`` (unified diff view)."""
    if agent.project_id is None:
        return TopicProgressListRead(items=[], total=0)

    if bundle is None:
        bundle = work_items.topic_work_items_bundle_for_agent(db, agent)
    all_items = bundle.items
    items: list[TopicProgressItemRead] = []
    for topic in bundle.open_topics:
        topic_items = [i for i in all_items if i.topic_id == topic.id]
        if not topic_items:
            continue
        comments = bundle.comments_by_topic.get(topic.id)
        item = work_items.topic_progress_item_from_work_items(
            db,
            topic,
            agent,
            topic_items,
            comments=comments,
        )
        if item is not None:
            items.append(item)
    return TopicProgressListRead(items=items, total=len(items))
