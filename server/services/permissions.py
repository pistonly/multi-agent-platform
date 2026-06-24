from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import Agent, AgentRole, Experiment, ReviewItem
from server.services.errors import ForbiddenError, NotFoundError


def is_admin(agent: Agent) -> bool:
    return agent.role == AgentRole.admin


def require_admin(agent: Agent) -> None:
    if not is_admin(agent):
        raise ForbiddenError("Admin role required")


def ensure_project_access(agent: Agent, project_id: uuid.UUID) -> None:
    if is_admin(agent):
        return
    if agent.project_id != project_id:
        raise ForbiddenError("Access denied to this project")


def ensure_experiment_access(db: Session, agent: Agent, experiment_id: uuid.UUID) -> Experiment:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None or experiment.deleted_at is not None:
        raise NotFoundError("Experiment not found")
    ensure_project_access(agent, experiment.project_id)
    return experiment


def ensure_review_item_access(db: Session, agent: Agent, item_id: uuid.UUID) -> ReviewItem:
    stmt = select(ReviewItem).where(ReviewItem.id == item_id).options(joinedload(ReviewItem.review))
    item = db.scalar(stmt)
    if item is None:
        raise NotFoundError("Review item not found")
    ensure_experiment_access(db, agent, item.review.experiment_id)
    return item


def resolve_project_id_for_agent(agent: Agent, project_id: uuid.UUID | None) -> uuid.UUID:
    if is_admin(agent):
        if project_id is None:
            raise ForbiddenError("Admin must specify project_id")
        return project_id
    if agent.project_id is None:
        raise ForbiddenError("Agent is not bound to a project")
    if project_id is not None and project_id != agent.project_id:
        raise ForbiddenError("Access denied to this project")
    return agent.project_id
