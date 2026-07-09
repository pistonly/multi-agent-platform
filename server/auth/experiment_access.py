"""Experiment access helpers — authz experiment (0e6926fa) PR1.

Centralizes the "creator OR admin" check that was previously scattered
across ``phase_service`` action methods and missing on the lock
endpoints. The two-gate pattern (404 first, then 403) is sequence-
sensitive — see ``ensure_experiment_creator_or_admin`` docstring.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment
from server.services.errors import ForbiddenError, NotFoundError
from server.services.permissions import ensure_project_access, is_admin


def _check_creator_or_admin(actor: Agent, experiment: Experiment) -> None:
    """Raise ``ForbiddenError`` unless ``actor`` is the experiment
    creator OR has the admin role.

    This is the *lower-level* check; it assumes access (existence +
    project scoping) has already been verified by
    ``ensure_experiment_access``. Callers must therefore invoke
    ``ensure_experiment_access`` first — calling ``_check_creator_or_admin``
    on an experiment the actor cannot see would leak resource existence.
    """
    if experiment.creator_agent_id == actor.id:
        return
    if is_admin(actor):
        return
    raise ForbiddenError(
        "Only the experiment creator or an admin can perform this action"
    )


def ensure_experiment_creator_or_admin(
    db: Session,
    actor: Agent,
    experiment_id: uuid.UUID,
) -> Experiment:
    """Two-gate experiment creator/admin guard.

    1. ``ensure_experiment_access`` — 404 if the experiment doesn't
       exist (or is soft-deleted), 403 if it's in another project. This
       MUST run first so we don't leak the existence of cross-project
       experiments via the second gate.
    2. ``_check_creator_or_admin`` — 403 if the actor is neither the
       creator nor an admin.

    Returns the loaded ``Experiment`` so callers can reuse it.
    """
    experiment = db.get(Experiment, experiment_id)
    if experiment is None or experiment.deleted_at is not None:
        raise NotFoundError("Experiment not found")
    ensure_project_access(actor, experiment.project_id)
    _check_creator_or_admin(actor, experiment)
    return experiment


__all__ = [
    "ensure_experiment_creator_or_admin",
    "_check_creator_or_admin",
]
