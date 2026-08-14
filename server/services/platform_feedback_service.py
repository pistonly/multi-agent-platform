from __future__ import annotations

import uuid
from datetime import datetime, timezone

from map_types.enums import FeedbackCategory, FeedbackStatus
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, PlatformFeedback
from server.domain.schemas import (
    PlatformFeedbackCreate,
    PlatformFeedbackRead,
    PlatformFeedbackUpdate,
)
from server.services.errors import NotFoundError


def feedback_to_read(db: Session, feedback: PlatformFeedback) -> PlatformFeedbackRead:
    author = db.get(Agent, feedback.author_agent_id)
    return PlatformFeedbackRead(
        id=feedback.id,
        author_agent_id=feedback.author_agent_id,
        author_name=author.name if author is not None else None,
        project_id=feedback.project_id,
        body=feedback.body,
        category=feedback.category,
        status=feedback.status,
        metadata_json=feedback.metadata_json,
        created_at=feedback.created_at,
        updated_at=feedback.updated_at,
        archived_at=feedback.archived_at,
    )


def create_feedback(
    db: Session,
    agent: Agent,
    payload: PlatformFeedbackCreate,
) -> PlatformFeedbackRead:
    # project_id is a source-context hint only; a project-bound agent defaults
    # to its own project, admin may leave it global (None). NOT an access boundary.
    project_id = payload.project_id if payload.project_id is not None else agent.project_id
    feedback = PlatformFeedback(
        author_agent_id=agent.id,
        project_id=project_id,
        body=payload.body,
        category=payload.category,
        status=FeedbackStatus.new,
        metadata_json=payload.metadata,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback_to_read(db, feedback)


def _get_feedback(db: Session, feedback_id: uuid.UUID) -> PlatformFeedback:
    feedback = db.get(PlatformFeedback, feedback_id)
    if feedback is None:
        raise NotFoundError("Feedback not found")
    return feedback


def get_feedback_read(db: Session, feedback_id: uuid.UUID) -> PlatformFeedbackRead:
    return feedback_to_read(db, _get_feedback(db, feedback_id))


def list_feedback(
    db: Session,
    *,
    status: FeedbackStatus | None = None,
    category: FeedbackCategory | None = None,
    project_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
    include_archived: bool = False,
) -> tuple[list[PlatformFeedbackRead], int]:
    stmt = select(PlatformFeedback)
    if not include_archived:
        stmt = stmt.where(PlatformFeedback.archived_at.is_(None))
    if status is not None:
        stmt = stmt.where(PlatformFeedback.status == status)
    if category is not None:
        stmt = stmt.where(PlatformFeedback.category == category)
    if project_id is not None:
        stmt = stmt.where(PlatformFeedback.project_id == project_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    stmt = (
        stmt.order_by(PlatformFeedback.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(db.scalars(stmt))
    return [feedback_to_read(db, f) for f in rows], total


def update_feedback(
    db: Session,
    feedback_id: uuid.UUID,
    payload: PlatformFeedbackUpdate,
) -> PlatformFeedbackRead:
    feedback = _get_feedback(db, feedback_id)
    data = payload.model_dump(exclude_unset=True)
    archived = data.pop("archived", None)
    for key, value in data.items():
        setattr(feedback, key, value)
    if archived is not None:
        feedback.archived_at = datetime.now(timezone.utc) if archived else None
    db.commit()
    db.refresh(feedback)
    return feedback_to_read(db, feedback)
