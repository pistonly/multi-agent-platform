"""Unit tests for authz experiment (0e6926fa) PR1 — two-gate experiment access.

The order-sensitivity of ``ensure_experiment_creator_or_admin`` is
load-bearing: a 404 must not leak existence via 403 (or vice versa)
when an actor probes a cross-project experiment.
"""

from __future__ import annotations

import uuid

import pytest

from server.auth.experiment_access import (
    _check_creator_or_admin,
    ensure_experiment_creator_or_admin,
)
from server.domain.models import Agent, AgentRole, Experiment, Project
from server.services.errors import ForbiddenError, NotFoundError
from server.services.auth import create_agent


def _project(db, suffix: str = "a") -> Project:
    return Project(
        project_key=f"auth-{suffix}",
        name=f"Auth {suffix}",
        workspace_path=f"/tmp/auth-{suffix}",
    )


def _agent(db, project: Project, name: str = "host", role: AgentRole = AgentRole.agent) -> Agent:
    agent, _ = create_agent(db, name, role, project_id=project.id)
    return agent


def _experiment(db, project: Project, creator: Agent) -> Experiment:
    return Experiment(
        project_id=project.id,
        creator_agent_id=creator.id,
        title="Test experiment",
        phase="draft",
    )


def test_ensure_creator_or_admin_passes_for_creator(db_session):
    project = _project(db_session, "p1")
    db_session.add(project)
    db_session.flush()
    creator = _agent(db_session, project, "host")
    exp = _experiment(db_session, project, creator)
    db_session.add(exp)
    db_session.commit()

    result = ensure_experiment_creator_or_admin(db_session, creator, exp.id)
    assert result.id == exp.id


def test_ensure_creator_or_admin_passes_for_admin(db_session):
    project = _project(db_session, "p2")
    db_session.add(project)
    db_session.flush()
    creator = _agent(db_session, project, "host-creator")
    admin = _agent(db_session, project, "ops", role=AgentRole.admin)
    exp = _experiment(db_session, project, creator)
    db_session.add(exp)
    db_session.commit()

    result = ensure_experiment_creator_or_admin(db_session, admin, exp.id)
    assert result.id == exp.id


def test_ensure_creator_or_admin_403_for_same_project_other_host(db_session):
    """Same-project host who isn't the creator → 403."""
    project = _project(db_session, "p3")
    db_session.add(project)
    db_session.flush()
    creator = _agent(db_session, project, "host-creator")
    other_host = _agent(db_session, project, "host-other")
    exp = _experiment(db_session, project, creator)
    db_session.add(exp)
    db_session.commit()

    with pytest.raises(ForbiddenError):
        ensure_experiment_creator_or_admin(db_session, other_host, exp.id)


def test_ensure_creator_or_admin_404_when_missing(db_session):
    """Missing experiment → 404 (NOT 403)."""
    project = _project(db_session, "p4")
    db_session.add(project)
    db_session.flush()
    actor = _agent(db_session, project, "host")

    with pytest.raises(NotFoundError):
        ensure_experiment_creator_or_admin(db_session, actor, uuid.uuid4())


def test_ensure_creator_or_admin_404_when_soft_deleted(db_session):
    """Soft-deleted experiment → 404 (existence must not leak)."""
    project = _project(db_session, "p5")
    db_session.add(project)
    db_session.flush()
    creator = _agent(db_session, project, "host-creator")
    exp = _experiment(db_session, project, creator)
    from datetime import UTC, datetime

    exp.deleted_at = datetime.now(UTC)
    db_session.add(exp)
    db_session.commit()

    with pytest.raises(NotFoundError):
        ensure_experiment_creator_or_admin(db_session, creator, exp.id)


def test_ensure_creator_or_admin_404_for_missing_before_403_for_creator_mismatch(db_session):
    """Order-sensitivity: when the experiment does NOT exist, the
    helper must raise 404 (never 403). When it DOES exist but the
    actor is the wrong creator, it must raise 403. The order matters
    because a 404 must not leak the actor's relation to a non-existent
    experiment (no useful signal); a 403 must not leak whether the
    actor would have been the creator.
    """
    # Case 1: missing experiment → 404 even for admin.
    project = _project(db_session, "missing")
    db_session.add(project)
    db_session.flush()
    admin = _agent(db_session, project, "ops", role=AgentRole.admin)
    with pytest.raises(NotFoundError):
        ensure_experiment_creator_or_admin(db_session, admin, uuid.uuid4())

    # Case 2: same-project non-creator non-admin → 403.
    project2 = _project(db_session, "wrong-creator")
    db_session.add(project2)
    db_session.flush()
    creator = _agent(db_session, project2, "host-creator")
    other = _agent(db_session, project2, "host-other")
    exp = _experiment(db_session, project2, creator)
    db_session.add(exp)
    db_session.commit()
    with pytest.raises(ForbiddenError):
        ensure_experiment_creator_or_admin(db_session, other, exp.id)


def test_check_creator_or_admin_unit(db_session):
    """The private helper raises on non-creator non-admin."""
    project = _project(db_session, "unit")
    db_session.add(project)
    db_session.flush()
    creator = _agent(db_session, project, "host-creator")
    other = _agent(db_session, project, "host-other")
    admin = _agent(db_session, project, "ops", role=AgentRole.admin)
    exp = _experiment(db_session, project, creator)
    db_session.add(exp)
    db_session.commit()

    # Creator passes
    _check_creator_or_admin(creator, exp)
    # Admin passes
    _check_creator_or_admin(admin, exp)
    # Same-project non-creator non-admin raises
    with pytest.raises(ForbiddenError):
        _check_creator_or_admin(other, exp)
