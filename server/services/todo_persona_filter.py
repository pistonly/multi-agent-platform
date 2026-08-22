"""Persona-based filtering for experiment review todo partitions."""

from __future__ import annotations

from map_types.enums import ExperimentPhase
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Experiment
from server.domain.schemas import ExperimentReviewInformationalRead
from server.services.experiment_capabilities_service import _reviews_for_current_plan
from server.services.review_service import _qualifying_non_creator_reviews

_ACTIVE_REVIEW_PHASES = (ExperimentPhase.review, ExperimentPhase.result_review)


def agent_sees_review_obligations(
    agent: Agent,
    *,
    include_all_partitions: bool = False,
) -> bool:
    """Host/participant personas omit review obligation buckets; reviewer/admin keep them.

    Persona identity follows ``Agent.persona`` (trailing ``-<persona>``
    name segment), so bootstrap projects' ``<project_key>-host`` /
    ``<project_key>-participant`` agents are filtered too.
    """
    if include_all_partitions and agent.role == AgentRole.admin:
        return True
    if agent.role == AgentRole.admin:
        return True
    return agent.persona not in ("host", "participant")


def list_experiment_review_informational(
    db: Session,
    agent: Agent,
) -> list[ExperimentReviewInformationalRead]:
    """Read-only summary for host/participant; does not populate obligation buckets."""
    if agent.project_id is None and agent.role != AgentRole.admin:
        return []

    stmt = (
        select(Experiment)
        .where(
            Experiment.deleted_at.is_(None),
            Experiment.phase.in_(_ACTIVE_REVIEW_PHASES),
        )
        .order_by(Experiment.updated_at.desc())
    )
    if agent.role != AgentRole.admin:
        stmt = stmt.where(Experiment.project_id == agent.project_id)

    return [
        ExperimentReviewInformationalRead(
            experiment_title=experiment.title,
            phase=experiment.phase,
            updated_at=experiment.updated_at,
            review_progress=_review_progress(db, experiment),
        )
        for experiment in db.scalars(stmt)
    ]


def _review_progress(db: Session, experiment: Experiment) -> str:
    if experiment.phase == ExperimentPhase.result_review:
        return "awaiting_result_approval"
    reviews = _reviews_for_current_plan(db, experiment)
    done = len(_qualifying_non_creator_reviews(reviews, experiment.creator_agent_id))
    return f"{done}/1"
