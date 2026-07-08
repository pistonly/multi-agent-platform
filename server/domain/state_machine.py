from dataclasses import dataclass

from server.domain.models import ExperimentPhase, ReviewItem, ReviewItemStatus


class StateMachineError(Exception):
    pass


TERMINAL_PHASES = frozenset({ExperimentPhase.done, ExperimentPhase.cancelled})


@dataclass
class ReviewItemTransitionContext:
    is_creator: bool
    is_reviewer: bool
    is_admin: bool
    via_plan_revision: bool = False


def can_approve(phase: ExperimentPhase, unreasonable_items: list[ReviewItem]) -> bool:
    if phase != ExperimentPhase.review:
        return False
    if not unreasonable_items:
        return True
    # I1(c): ``closed`` is the new single terminal; legacy rows still carrying
    # ``resolved`` / ``withdrawn`` are accepted for backward compatibility.
    return all(
        item.status
        in (
            ReviewItemStatus.closed,
            ReviewItemStatus.resolved,
            ReviewItemStatus.withdrawn,
        )
        for item in unreasonable_items
        if item.status is not None
    )


def validate_phase_transition(current: ExperimentPhase, target: ExperimentPhase) -> None:
    allowed: dict[ExperimentPhase, set[ExperimentPhase]] = {
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
    if current in TERMINAL_PHASES:
        raise StateMachineError(f"Cannot transition from terminal phase '{current.value}'")
    if target not in allowed.get(current, set()):
        raise StateMachineError(f"Invalid phase transition: {current.value} -> {target.value}")


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
