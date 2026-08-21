"""Per-agent experiment actions and blocked_on (experiment plan v2 AC#3)."""

from __future__ import annotations

import uuid
from typing import Any

from map_types.enums import PhaseOwner
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    ExperimentLog,
    ExperimentPhase,
    Project,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
)
from server.domain.schemas import ExperimentDetailRead, ExperimentSummaryRead
from server.services.review_service import (
    _prior_version_reviews_fully_resolved,
    _qualifying_non_creator_reviews,
    _review_has_item_activity,
    compute_legacy_self_review,
    count_open_unreasonable_for_experiment,
    has_review_on_older_plan_version,
)

_REPLY_STATES = (ReviewItemStatus.addressed, ReviewItemStatus.rebutted)

# 0db51e10 I3(5d): phase visibility whitelist. ``hidden_for_current_persona``
# is the per-actor fold semantics for phases excluded by the configured
# whitelist. Empty / ``None`` whitelist = every phase visible (backward
# compat). When a whitelist is configured, phases outside it return
# ``([], "hidden_for_current_persona")`` so the UI can fold the row
# without raising.
HIDDEN_FOR_CURRENT_PERSONA = "hidden_for_current_persona"


def _is_phase_visible(
    phase: ExperimentPhase,
    *,
    phase_whitelist: list[ExperimentPhase] | None,
) -> bool:
    """Return True iff ``phase`` is in the configured whitelist.

    ``None`` or empty whitelist = visible to every role (legacy
    behaviour). When the whitelist is non-empty the predicate is
    exact-match against ``phase.value`` so callers can pass either
    ``[ExperimentPhase.result_review]`` or
    ``["result_review"]`` interchangeably.
    """
    if not phase_whitelist:
        return True
    allowed = {p.value if isinstance(p, ExperimentPhase) else str(p) for p in phase_whitelist}
    return phase.value in allowed


def _reviews_for_current_plan(db: Session, experiment: Experiment) -> list[Review]:
    return list(
        db.scalars(
            select(Review)
            .where(
                Review.experiment_id == experiment.id,
                Review.plan_version == experiment.current_plan_version,
            )
            .options(joinedload(Review.items))
        ).unique()
    )


def _has_non_creator_review(reviews: list[Review], creator_id: uuid.UUID) -> bool:
    return len(_qualifying_non_creator_reviews(reviews, creator_id)) > 0


def _actor_review_for_current_plan(reviews: list[Review], actor_id: uuid.UUID) -> Review | None:
    for review in reviews:
        if review.reviewer_agent_id == actor_id:
            return review
    return None


def _count_open_status_unreasonable(db: Session, experiment_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status == ReviewItemStatus.open,
        )
    )
    return db.scalar(stmt) or 0


def _reviewer_has_pending_addressed_items(
    db: Session, experiment_id: uuid.UUID, actor_id: uuid.UUID
) -> bool:
    stmt = (
        select(func.count())
        .select_from(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            Review.reviewer_agent_id == actor_id,
            Review.archived_at.is_(None),
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status.in_(_REPLY_STATES),
        )
    )
    return (db.scalar(stmt) or 0) > 0


def compute_experiment_capabilities(
    db: Session,
    experiment: Experiment,
    actor: Agent,
    *,
    phase_whitelist: list[ExperimentPhase] | None = None,
) -> tuple[list[str], str | None]:
    is_creator = actor.id == experiment.creator_agent_id
    is_admin = actor.role == AgentRole.admin
    phase = experiment.phase

    # 0db51e10 I3(5d): phase visibility whitelist. When configured and
    # the current phase is NOT in the whitelist, fold the row as
    # ``hidden_for_current_persona`` so the UI can collapse it without
    # raising. Whitelist is dynamic — callers pass the resolved value
    # from server config / per-test override, no module-level global.
    if not _is_phase_visible(phase, phase_whitelist=phase_whitelist):
        return [], HIDDEN_FOR_CURRENT_PERSONA

    actions: list[str] = []
    blocked_on: str | None = None

    if phase == ExperimentPhase.result_review:
        if is_creator:
            return [], "awaiting_result_approval"
        return ["accept_result", "reject_result"], "none"

    if _reviewer_has_pending_addressed_items(db, experiment.id, actor.id):
        return ["resolve_item"], "awaiting_addressed_item_ack"

    if phase == ExperimentPhase.draft:
        if is_creator or is_admin:
            # v0.10: direct mode skips review — host can start directly.
            if getattr(experiment, "mode", "standard") == "direct":
                return ["start"], "none"
            return ["submit_for_review"], "none"
        return actions, blocked_on

    if phase == ExperimentPhase.review:
        reviews = _reviews_for_current_plan(db, experiment)
        if is_creator or is_admin:
            open_unreasonable = count_open_unreasonable_for_experiment(db, experiment.id)
            if open_unreasonable > 0:
                blocked_on = "open_unreasonable_item"
                if _count_open_status_unreasonable(db, experiment.id) > 0:
                    actions.append("plan_revise")
            elif not _has_non_creator_review(reviews, experiment.creator_agent_id):
                if _prior_version_reviews_fully_resolved(db, experiment):
                    # Reviewer resolved every unreasonable item they raised on a
                    # prior plan version → counts as accepting the revision, so
                    # the creator may approve without a fresh current-version
                    # review (mirrors assert_approve_eligibility's carve-out).
                    blocked_on = "none"
                    actions = ["approve", "withdraw"]
                elif has_review_on_older_plan_version(db, experiment):
                    blocked_on = "awaiting_review_for_current_plan_version"
                else:
                    blocked_on = "awaiting_non_creator_review"
            else:
                blocked_on = "none"
                actions = ["approve", "withdraw"]
            return actions, blocked_on

        if actor.id != experiment.creator_agent_id:
            actor_review = _actor_review_for_current_plan(reviews, actor.id)
            if actor_review is None:
                return ["review_add"], "none"
            if not _review_has_item_activity(actor_review):
                return ["review_withdraw"], "none"
        return actions, blocked_on

    if is_creator or is_admin:
        if phase == ExperimentPhase.approved:
            return ["start"], "none"
        if phase == ExperimentPhase.running:
            # v0.10: in direct mode, completion is delegated to the
            # executor (usually participant). But if the host self-
            # executes (no --executor given), they can still complete.
            if getattr(experiment, "mode", "standard") == "direct":
                if actor.id == experiment.executor_agent_id:
                    return ["complete"], "none"
                return [], "waiting_for_executor"
            return ["complete"], "none"

    # v0.10: in direct mode, the designated executor (participant) can
    # complete the experiment during the running phase.
    if (
        phase == ExperimentPhase.running
        and getattr(experiment, "mode", "standard") == "direct"
        and experiment.executor_agent_id is not None
        and actor.id == experiment.executor_agent_id
    ):
        return ["complete"], "none"

    return actions, blocked_on


def experiment_summary_for_actor(
    db: Session,
    experiment: Experiment,
    actor: Agent,
    *,
    extra_updates: dict[str, Any] | None = None,
    phase_whitelist: list[ExperimentPhase] | None = None,
) -> ExperimentSummaryRead:
    from server.services.log_service import get_latest_log
    from server.services.phase_owner_resolver import (
        is_informational_only,
        owner_for,
    )

    actions, blocked_on = compute_experiment_capabilities(
        db, experiment, actor, phase_whitelist=phase_whitelist
    )
    legacy = compute_legacy_self_review(db, experiment)
    # log_count / latest_log_summary: skip the per-experiment SELECTs
    # when extra_updates already provides them — looping callers (e.g.
    # get_todos over many experiments) pre-compute via the bulk helpers
    # in log_service to drop the N+1.
    has_log_count = extra_updates is not None and "log_count" in extra_updates
    has_latest_summary = (
        extra_updates is not None and "latest_log_summary" in extra_updates
    )
    if has_log_count and extra_updates is not None:
        log_count: int = extra_updates["log_count"]
    else:
        log_count = (
            db.scalar(
                select(func.count())
                .select_from(ExperimentLog)
                .where(ExperimentLog.experiment_id == experiment.id)
            )
            or 0
        )
    if has_latest_summary and extra_updates is not None:
        latest_log_summary: str | None = extra_updates["latest_log_summary"]
    else:
        latest_log = get_latest_log(db, experiment.id)
        latest_log_summary = latest_log.summary if latest_log else None

    exp_mode = getattr(experiment, "mode", "standard")

    update: dict[str, Any] = {
        "actions": actions,
        "blocked_on": blocked_on,
        "legacy_self_review": legacy,
        "log_count": log_count,
        "latest_log_summary": latest_log_summary,
        "phase_owner": owner_for(experiment.phase, mode=exp_mode),
        "informational_only": is_informational_only(
            experiment.phase, actions=actions, blocked_on=blocked_on, mode=exp_mode
        ),
        "hidden_for_current_persona": (
            actions == []
            and blocked_on == HIDDEN_FOR_CURRENT_PERSONA
        ),
    }
    if extra_updates:
        update.update(extra_updates)
    project = db.get(Project, experiment.project_id)
    if project is not None:
        from server.services.fs_source_service import content_source_meta

        update["source"] = content_source_meta(db, project)
    return ExperimentSummaryRead.model_validate(experiment).model_copy(update=update)


def apply_capabilities_to_detail(
    detail: ExperimentDetailRead,
    actions: list[str],
    blocked_on: str | None,
    *,
    legacy_self_review: bool = False,
    phase_owner: PhaseOwner | None = None,
    informational_only: bool | None = None,
) -> ExperimentDetailRead:
    update: dict[str, Any] = {
        "actions": actions,
        "blocked_on": blocked_on,
        "legacy_self_review": legacy_self_review,
    }
    if phase_owner is not None:
        update["phase_owner"] = phase_owner
    if informational_only is not None:
        update["informational_only"] = informational_only
    return detail.model_copy(update=update)
