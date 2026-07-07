"""Unified agent work snapshot (whoami + topic-progress + todos + wakeable notifications)."""

from __future__ import annotations

from map_types.enums import NotificationCategory
from sqlalchemy.orm import Session

from server.domain.models import Agent
from server.domain.schemas import (
    AgentRead,
    AgentWorkRead,
    NotificationListRead,
    NotificationRead,
)
from server.services import notification_service, todo_service, topic_progress_service
from server.services import project_service as svc
from server.services import topic_work_item_service as work_items


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

    return AgentWorkRead(
        agent=agent_read,
        topic_progress=topic_progress,
        todos=todos,
        notifications=notifications,
    )
