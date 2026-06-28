from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    AgentRole,
    Comment,
    Experiment,
    PlanVersion,
    ProjectStatusVersion,
    Review,
    ReviewItem,
    Topic,
    TopicComment,
)
from server.services.errors import ForbiddenError, NotFoundError, UnauthorizedError


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


def ensure_can_revise_project_status(agent: Agent, project_id: uuid.UUID) -> None:
    """Admin or project-bound agent (e.g. host persona) may revise Current Status MD."""
    ensure_project_access(agent, project_id)


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


def ensure_topic_access(db: Session, agent: Agent, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or topic.deleted_at is not None:
        raise NotFoundError("Topic not found")
    ensure_project_access(agent, topic.project_id)
    return topic


def ensure_topic_comment_access(db: Session, agent: Agent, comment_id: uuid.UUID) -> TopicComment:
    comment = db.get(TopicComment, comment_id)
    if comment is None:
        raise NotFoundError("Topic comment not found")
    ensure_topic_access(db, agent, comment.topic_id)
    return comment


def ensure_audit_target_access(
    db: Session,
    agent: Agent,
    target_type: str,
    target_id: uuid.UUID,
) -> None:
    if target_type == "experiment":
        ensure_experiment_access(db, agent, target_id)
    elif target_type == "topic":
        ensure_topic_access(db, agent, target_id)
    elif target_type == "plan_version":
        plan = db.get(PlanVersion, target_id)
        if plan is None:
            raise NotFoundError("Plan version not found")
        ensure_experiment_access(db, agent, plan.experiment_id)
    elif target_type == "review":
        review = db.get(Review, target_id)
        if review is None:
            raise NotFoundError("Review not found")
        ensure_experiment_access(db, agent, review.experiment_id)
    elif target_type == "comment":
        comment = db.get(Comment, target_id)
        if comment is None:
            raise NotFoundError("Comment not found")
        ensure_experiment_access(db, agent, comment.experiment_id)
    elif target_type == "project_status_version":
        version = db.get(ProjectStatusVersion, target_id)
        if version is None:
            raise NotFoundError("Project status version not found")
        ensure_project_access(agent, version.project_id)
    else:
        raise NotFoundError(f"Unknown audit target type: {target_type}")


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


def ensure_can_register_agent(db: Session, actor: Agent | None, role: AgentRole) -> None:
    """Allow unauthenticated bootstrap of the first admin; thereafter require admin."""
    from sqlalchemy import func, select

    count = db.scalar(select(func.count()).select_from(Agent)) or 0
    if count == 0:
        if role != AgentRole.admin:
            raise ForbiddenError("Bootstrap: first agent must have role=admin")
        return
    if actor is None:
        raise UnauthorizedError("Authentication required")
    require_admin(actor)
