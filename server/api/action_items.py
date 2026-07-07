"""Action-item lifecycle endpoints (experiment A1).

Only the explicit close paths (``complete`` / ``cancel``) live here; ``list``
stays under ``/projects/{id}/action-items`` in projects.py for backward
compatibility with the existing ``map action list`` CLI.

Experiment B (plan §3 / I4): also exposes ``mark-wake-sent`` and ``mark-stale``
so the runtime-waker CLI process can advance the three-stage escalation
timeline without a direct DB session. Both endpoints delegate to the public
``topic_service.mark_wake_sent_action_item`` / ``mark_stale_action_item``
wrappers which run the access check + commit + read-back.
"""

import uuid

from fastapi import APIRouter, Depends, status
from map_types.schemas import ActionItemCancel, TopicActionItemRead
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.services import topic_service

action_items_router = APIRouter(tags=["action_items"])


@action_items_router.post(
    "/action-items/{action_item_id}/complete",
    response_model=TopicActionItemRead,
    status_code=status.HTTP_200_OK,
)
def complete_action_item(
    action_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicActionItemRead:
    """Move an open action item to ``done``.

    Returns 404 if the item doesn't exist, 403 if the caller is neither the
    owner nor admin, 409 if the item is already ``done`` or ``cancelled``.
    """
    return topic_service.complete_action_item(db, action_item_id, agent)


@action_items_router.post(
    "/action-items/{action_item_id}/deliver",
    response_model=TopicActionItemRead,
    status_code=status.HTTP_200_OK,
)
def deliver_action_item(
    action_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicActionItemRead:
    """Deliver (complete) an open action item when its source topic is closed/archived."""
    return topic_service.deliver_action_item(db, action_item_id, agent)


@action_items_router.post(
    "/action-items/{action_item_id}/cancel",
    response_model=TopicActionItemRead,
    status_code=status.HTTP_200_OK,
)
def cancel_action_item(
    action_item_id: uuid.UUID,
    payload: ActionItemCancel,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicActionItemRead:
    """Move an open action item to ``cancelled`` and persist cancel_reason.

    Reason length is validated per-category by ``ActionItemCancel`` so the
    CLI layer can't bypass the API gate (A1 acceptance A1-3 / A1-4).
    """
    return topic_service.cancel_action_item(db, action_item_id, agent, payload)


@action_items_router.post(
    "/action-items/{action_item_id}/link",
    response_model=TopicActionItemRead,
    status_code=status.HTTP_200_OK,
)
def link_action_item(
    action_item_id: uuid.UUID,
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicActionItemRead:
    """Attach an experiment to an open action item (``linked_experiment_id``).

    Used to enable the ``experiment.completed → action_item.done`` cascade
    when the action item was created without an explicit link. Owner or admin
    only; cross-project links are rejected with 409.
    """
    return topic_service.link_action_item(db, action_item_id, experiment_id, agent)


@action_items_router.post(
    "/action-items/{action_item_id}/mark-wake-sent",
    response_model=TopicActionItemRead,
    status_code=status.HTTP_200_OK,
)
def mark_wake_sent_endpoint(
    action_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicActionItemRead:
    """Bump ``wake_count`` + stamp ``last_woken_at`` + audit row.

    Called by the runtime-waker CLI process when ``should_wake_action_item``
    returns ``'wake'`` (experiment B, plan §3 / I4). Owner or admin only —
    mirrors the access model used by ``complete`` / ``cancel`` so a waker
    driven by a non-owner admin agent can still escalate a forgotten item.
    """
    return topic_service.mark_wake_sent_action_item(db, action_item_id, agent)


@action_items_router.post(
    "/action-items/{action_item_id}/mark-stale",
    response_model=TopicActionItemRead,
    status_code=status.HTTP_200_OK,
)
def mark_stale_endpoint(
    action_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicActionItemRead:
    """Stamp ``stale_at`` + write the ``action_item.stale`` audit row.

    Called by the runtime-waker CLI process when ``should_wake_action_item``
    returns ``'stale'`` (plan §3 / I4 — the 4th unanswered wake). Admin-only
    by design: a stale transition is a system-level escalation that should
    not be triggerable by the assignee themselves. ``admin_notified=True``
    and ``creator_audit_only=True`` are passed by default per plan §3 §6
    (3c admin 优先 + 6 creator 不 wake); I6 will wire actual admin delivery.
    """
    return topic_service.mark_stale_action_item(db, action_item_id, agent)
