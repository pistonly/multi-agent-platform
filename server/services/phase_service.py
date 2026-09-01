import uuid
from typing import cast

from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    AgentRole,
    ExperimentPhase,
)
from server.domain.state_machine import validate_phase_transition
from server.services.errors import ForbiddenError, StateTransitionError
from server.services.phase_owner_resolver import owner_for
from server.services.project_service import get_experiment
from server.services.review_service import (
    assert_approve_eligibility,
)


def _sync_phase_owner(experiment) -> None:
    """Mirror ``experiment.phase_owner`` to the resolver's table.

    Called immediately after every ``experiment.phase = ...`` assignment
    in this module so the two never drift. Kept as a thin wrapper so a
    future override path (e.g. admin override, experiment.phase_owner
    column hand-edit) can plug in here without touching every site.

    In ``direct`` mode (v0.10), the resolver routes ``running`` to
    ``participant`` instead of ``host``.

    Type-annotation uses a string forward reference (``Experiment``) so
    this helper can live next to its callers without pulling the heavy
    model imports into this module's top-level namespace.
    """
    experiment.phase_owner = owner_for(
        experiment.phase, mode=getattr(experiment, "mode", "standard")
    ).value


def _ensure_creator_or_admin(experiment, actor: Agent) -> None:
    """Host-only lifecycle gate: creator OR admin.

    Used by create / submit-review / approve / start / withdraw / cancel.
    The host retains decision authority for these transitions even when
    execution has been delegated to another agent via
    ``executor_agent_id`` (migration 042).
    """
    if experiment.creator_agent_id == actor.id:
        return
    if actor.role == AgentRole.admin:
        return
    raise ForbiddenError("Only the experiment creator or an admin can perform this action")


def _resolve_executor_id(experiment) -> uuid.UUID:
    """Return the agent_id authorized to call ``complete``.

    Falls back to ``creator_agent_id`` for legacy experiments where
    ``executor_agent_id`` is NULL (created before migration 042) so the
    host-self-executes behavior is preserved.
    """
    return cast(uuid.UUID, experiment.executor_agent_id or experiment.creator_agent_id)


def _ensure_can_complete(experiment, actor: Agent) -> None:
    """Executor gate for ``complete_experiment``.

    Only the designated executor (or an admin) may submit the result.
    For legacy experiments (``executor_agent_id IS NULL``) the creator
    remains the executor — preserving the pre-042 host-self-executes
    behavior with zero migration cost.
    """
    if actor.role == AgentRole.admin:
        return
    if actor.id == _resolve_executor_id(experiment):
        return
    raise ForbiddenError(
        "Only the designated executor (or an admin) can complete the experiment"
    )


def submit_for_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    if experiment.current_plan_version < 1:
        raise StateTransitionError("Experiment must have a plan before review")
    validate_phase_transition(experiment.phase, ExperimentPhase.review, mode=experiment.mode)
    experiment.phase = ExperimentPhase.review
    _sync_phase_owner(experiment)
    db.commit()


def approve_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    assert_approve_eligibility(db, experiment)
    validate_phase_transition(experiment.phase, ExperimentPhase.approved, mode=experiment.mode)
    experiment.phase = ExperimentPhase.approved
    _sync_phase_owner(experiment)
    db.commit()


def withdraw_from_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    validate_phase_transition(experiment.phase, ExperimentPhase.draft, mode=experiment.mode)
    experiment.phase = ExperimentPhase.draft
    _sync_phase_owner(experiment)
    db.commit()


def cancel_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    validate_phase_transition(experiment.phase, ExperimentPhase.cancelled, mode=experiment.mode)
    experiment.phase = ExperimentPhase.cancelled
    _sync_phase_owner(experiment)
    db.commit()


def start_experiment(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    executor_agent_id: uuid.UUID | None = None,
) -> None:
    """Transition to ``running`` and optionally delegate execution.

    Standard mode: ``approved → running``.
    Direct mode (v0.10): ``draft → running`` (skips review/approved).

    ``actor`` must be the host creator (or admin) — the host retains the
    decision to *start* the experiment. ``executor_agent_id`` designates
    who may call ``complete`` later:

    - ``None`` (default): the host self-executes; ``executor_agent_id``
      is set to ``actor.id`` so the field is always populated on new
      experiments.
    - A different agent's UUID (typically a ``participant`` persona):
      that agent becomes the sole non-admin caller allowed to submit
      the result via ``complete``. The host gives up the ``complete``
      permission but keeps every other lifecycle gate.

    The designated executor must be a member of the same project; an
    out-of-project ``executor_agent_id`` is rejected with 403.
    """
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    # Default: host self-executes. Explicit None means "I'll run it
    # myself" — populate the column so downstream code can rely on
    # ``executor_agent_id`` being non-null for new experiments.
    resolved_executor_id = executor_agent_id or actor.id
    # Validate the executor is a member of the same project. Admins
    # bypass this so an admin host can delegate cross-project (rare
    # but supported for ops scenarios).
    if resolved_executor_id != actor.id and actor.role != AgentRole.admin:
        from server.domain.models import Agent as _AgentModel

        executor = db.get(_AgentModel, resolved_executor_id)
        if executor is None:
            raise ForbiddenError(
                f"Executor agent {resolved_executor_id} not found"
            )
        if executor.project_id != experiment.project_id:
            raise ForbiddenError(
                "Executor must be a member of the same project as the experiment"
            )
    validate_phase_transition(experiment.phase, ExperimentPhase.running, mode=experiment.mode)
    experiment.executor_agent_id = resolved_executor_id
    experiment.phase = ExperimentPhase.running
    _sync_phase_owner(experiment)
    db.commit()


# ---------------------------------------------------------------------------
# T45: 完成域（complete/accept/reject + pytest gate + verdict 校验）拆至
# ``phase_completion``。此处 re-export 维持既有导入面——api/ 层与测试的
# ``from server.services import phase_service`` + 属性访问不变；依赖方向为
# phase_completion --lazy--> phase_service（helpers call-time 导入），无导入环。
from server.services.phase_completion import (  # noqa: E402,F401
    _apply_pytest_summary_gate,
    _build_completion_log,
    _complete_direct_mode,
    _complete_standard_mode,
    _ensure_result_reviewer,
    _format_pytest_summary_validation,
    _legacy_accept_result_metadata,
    _notify_topic_close_pending,
    _recheck_pytest_summary_for_accept,
    _validate_and_summarize_verdict_file,
    _validation_metadata,
    accept_result,
    complete_experiment,
    raise_reject_result_misuse,
    reject_result,
)
