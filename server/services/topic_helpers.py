"""Shared topic lookup helpers.

Split out so lifecycle / resolve / action-item modules can share
``_get_topic`` and ``_agent_names_by_ids`` without circular imports.
``topic_service`` re-exports both for existing private callers
(e.g. ``topic_work_item_service``).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Topic
from server.services.errors import NotFoundError


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
