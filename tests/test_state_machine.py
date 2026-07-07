import pytest

from server.domain.models import ExperimentPhase, ReviewItem, ReviewItemKind, ReviewItemStatus
from server.domain.state_machine import (
    ReviewItemTransitionContext,
    StateMachineError,
    can_approve,
    count_open_unreasonable,
    validate_phase_transition,
    validate_review_item_transition,
)


def test_phase_transitions():
    validate_phase_transition(ExperimentPhase.draft, ExperimentPhase.review)
    validate_phase_transition(ExperimentPhase.review, ExperimentPhase.approved)
    validate_phase_transition(ExperimentPhase.running, ExperimentPhase.result_review)
    validate_phase_transition(ExperimentPhase.result_review, ExperimentPhase.done)
    validate_phase_transition(ExperimentPhase.result_review, ExperimentPhase.running)
    with pytest.raises(StateMachineError):
        validate_phase_transition(ExperimentPhase.draft, ExperimentPhase.approved)


def test_review_item_transitions():
    ctx_creator = ReviewItemTransitionContext(is_creator=True, is_reviewer=False, is_admin=False)
    ctx_reviewer = ReviewItemTransitionContext(is_creator=False, is_reviewer=True, is_admin=False)
    validate_review_item_transition(
        ReviewItemStatus.open,
        ReviewItemStatus.rebutted,
        ctx_creator,
    )
    validate_review_item_transition(
        ReviewItemStatus.addressed,
        ReviewItemStatus.resolved,
        ctx_reviewer,
    )


def test_can_approve():
    assert can_approve(ExperimentPhase.review, [])
    items = [
        _item(ReviewItemStatus.resolved),
        _item(ReviewItemStatus.withdrawn),
    ]
    assert can_approve(ExperimentPhase.review, items)
    assert not can_approve(ExperimentPhase.review, [_item(ReviewItemStatus.open)])
    assert not can_approve(ExperimentPhase.draft, [])


def test_count_open_unreasonable():
    items = [
        _item(ReviewItemStatus.open),
        _item(ReviewItemStatus.resolved),
        _item(ReviewItemStatus.addressed),
    ]
    assert count_open_unreasonable(items) == 2


def _item(status: ReviewItemStatus) -> ReviewItem:
    return ReviewItem(kind=ReviewItemKind.unreasonable, content="x", status=status)
