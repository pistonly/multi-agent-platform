from dataclasses import dataclass

from map_types.enums import ExperimentMode, ExperimentPhase, ReviewItemStatus
from server.domain.models import ReviewItem


class StateMachineError(Exception):
    pass


TERMINAL_PHASES = frozenset({ExperimentPhase.done, ExperimentPhase.cancelled})

_DIRECT_ALLOWED: dict[ExperimentPhase, set[ExperimentPhase]] = {
    ExperimentPhase.draft: {ExperimentPhase.running, ExperimentPhase.cancelled},
    ExperimentPhase.running: {ExperimentPhase.done, ExperimentPhase.cancelled},
    ExperimentPhase.done: set(),
    ExperimentPhase.cancelled: set(),
}

_STANDARD_ALLOWED: dict[ExperimentPhase, set[ExperimentPhase]] = {
    ExperimentPhase.draft: {ExperimentPhase.review, ExperimentPhase.cancelled},
    ExperimentPhase.review: {
        ExperimentPhase.approved,
        ExperimentPhase.draft,
        ExperimentPhase.cancelled,
    },
    ExperimentPhase.approved: {ExperimentPhase.running, ExperimentPhase.cancelled},
    ExperimentPhase.running: {ExperimentPhase.result_review, ExperimentPhase.cancelled},
    ExperimentPhase.result_review: {
        ExperimentPhase.done,
        ExperimentPhase.running,
        ExperimentPhase.cancelled,
    },
    ExperimentPhase.done: set(),
    ExperimentPhase.cancelled: set(),
}


@dataclass
class ReviewItemTransitionContext:
    is_creator: bool
    is_reviewer: bool
    is_admin: bool
    via_plan_revision: bool = False


def validate_phase_transition(
    current: ExperimentPhase,
    target: ExperimentPhase,
    *,
    mode: str = ExperimentMode.standard.value,
) -> None:
    if current in TERMINAL_PHASES:
        raise StateMachineError(f"Cannot transition from terminal phase '{current.value}'")
    table = _DIRECT_ALLOWED if mode == ExperimentMode.direct.value else _STANDARD_ALLOWED
    if target not in table.get(current, set()):
        raise StateMachineError(
            f"Invalid phase transition: {current.value} -> {target.value}"
            f" (mode={mode})"
        )


def validate_review_item_transition(
    current: ReviewItemStatus,
    target: ReviewItemStatus,
    ctx: ReviewItemTransitionContext,
) -> None:
    transitions: dict[tuple[ReviewItemStatus, ReviewItemStatus], callable] = {
        (ReviewItemStatus.open, ReviewItemStatus.addressed): lambda c: c.via_plan_revision,
        (ReviewItemStatus.open, ReviewItemStatus.rebutted): lambda c: c.is_creator,
        (ReviewItemStatus.open, ReviewItemStatus.withdrawn): lambda c: c.is_reviewer,
        (ReviewItemStatus.open, ReviewItemStatus.escalated): lambda c: c.is_reviewer or c.is_admin,
        (ReviewItemStatus.addressed, ReviewItemStatus.resolved): lambda c: c.is_reviewer,
        (ReviewItemStatus.addressed, ReviewItemStatus.open): lambda c: c.is_reviewer,
        (ReviewItemStatus.rebutted, ReviewItemStatus.resolved): lambda c: c.is_reviewer,
        (ReviewItemStatus.rebutted, ReviewItemStatus.open): lambda c: c.is_reviewer,
        (ReviewItemStatus.escalated, ReviewItemStatus.resolved): lambda c: c.is_reviewer or c.is_admin,
    }
    key = (current, target)
    if key not in transitions:
        raise StateMachineError(f"Invalid review item transition: {current.value} -> {target.value}")
    if not transitions[key](ctx):
        raise StateMachineError("Actor is not allowed to perform this review item transition")


def count_open_unreasonable(unreasonable_items: list[ReviewItem]) -> int:
    return sum(
        1
        for item in unreasonable_items
        if item.status
        in (
            ReviewItemStatus.open,
            ReviewItemStatus.addressed,
            ReviewItemStatus.rebutted,
            ReviewItemStatus.escalated,
        )
    )
