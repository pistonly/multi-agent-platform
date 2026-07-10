"""Unit tests for ``server.services.lock_service``.

Covers race experiment (eca0f522) PR1:

* Happy path: ``acquire`` then ``release`` round-trips.
* Conflict: ``acquire`` on a different experiment in the same project
  while a live holder exists raises ``ConflictError`` (caught by
  ``_find_project_holder``).
* DB-enforced race: when ``commit_with_retry`` raises ``IntegrityError``
  (the ``uq_experiment_lock_holder_active`` partial unique index on PG
  / plain unique index on SQLite catches the duplicate), ``acquire``
  translates to ``ConflictError`` with a project-scoped message.
* Force-release clears all holders on the project and uses the retry
  helper.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from server.domain.models import Agent, AgentRole, Experiment, ExperimentPhase, Project
from server.services import lock_service
from server.services.auth import create_agent
from server.services.errors import ConflictError


def _project(db_session) -> Project:
    project = Project(project_key="lock-svc", name="Lock Svc", workspace_path="/tmp/lock-svc")
    db_session.add(project)
    db_session.flush()
    return project


def _host(db_session, project: Project) -> Agent:
    agent, _ = create_agent(db_session, "host-svc", AgentRole.agent, project_id=project.id)
    return agent


def _running_experiment(db_session, project: Project, host: Agent, title: str) -> Experiment:
    exp = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        title=title,
        phase=ExperimentPhase.running,
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()
    return exp


def test_acquire_then_release_roundtrip(db_session):
    project = _project(db_session)
    host = _host(db_session, project)
    exp = _running_experiment(db_session, project, host, "happy")

    result = lock_service.acquire_experiment_lock(db_session, exp.id, host)
    assert result.holder == exp.id
    assert result.acquired_at is not None

    released = lock_service.release_experiment_lock(db_session, exp.id, host)
    assert released.holder is None


def test_acquire_raises_conflict_when_other_holds(db_session):
    """In-process _find_project_holder guard catches the conflict first."""
    project = _project(db_session)
    host = _host(db_session, project)
    a = _running_experiment(db_session, project, host, "exp-a")
    b = _running_experiment(db_session, project, host, "exp-b")

    lock_service.acquire_experiment_lock(db_session, a.id, host)
    with pytest.raises(ConflictError) as exc:
        lock_service.acquire_experiment_lock(db_session, b.id, host)
    assert str(a.id) in str(exc.value)


def test_acquire_integrity_error_translates_to_conflict(db_session):
    """DB-layer race: the partial unique index (PG) / plain unique index
    (SQLite) fires ``IntegrityError`` on commit. ``acquire_experiment_lock``
    must catch it and re-raise as ``ConflictError`` instead of letting
    the SQLAlchemy exception escape to the API caller.
    """
    project = _project(db_session)
    host = _host(db_session, project)
    exp = _running_experiment(db_session, project, host, "exp-race")

    def fake_commit(db, **kwargs):
        # Simulate the IntegrityError that the unique partial index
        # would raise on PG when two acquires race past the
        # ``_find_project_holder`` check.
        db.rollback()
        raise IntegrityError(
            "stmt",
            {},
            Exception("duplicate key value violates unique constraint"),
        )

    with patch.object(lock_service, "commit_with_retry", side_effect=fake_commit), pytest.raises(ConflictError):
        lock_service.acquire_experiment_lock(db_session, exp.id, host)


def test_acquire_reclaim_after_expiry(db_session):
    project = _project(db_session)
    host = _host(db_session, project)
    a = _running_experiment(db_session, project, host, "exp-a")
    b = _running_experiment(db_session, project, host, "exp-b")

    # Manually set A as a stale holder (TTL expired).
    a.lock_holder_experiment_id = a.id
    a.lock_acquired_at = datetime(2020, 1, 1, tzinfo=UTC)
    a.lock_ttl_seconds = 60
    db_session.flush()

    # B should reclaim transparently.
    result = lock_service.acquire_experiment_lock(db_session, b.id, host)
    assert result.holder == b.id


def test_force_release_clears_all_holders(db_session):
    project = _project(db_session)
    host = _host(db_session, project)
    a = _running_experiment(db_session, project, host, "exp-a")
    b = _running_experiment(db_session, project, host, "exp-b")

    a.lock_holder_experiment_id = a.id
    a.lock_acquired_at = datetime.now(UTC)
    a.lock_ttl_seconds = 1000
    b.lock_holder_experiment_id = b.id  # pathological state for test
    b.lock_acquired_at = datetime.now(UTC)
    b.lock_ttl_seconds = 1000
    db_session.flush()

    result = lock_service.force_release_experiment_lock(
        db_session, a.id, host, reason="test cleanup"
    )
    assert result.holder is None
    db_session.refresh(b)
    assert b.lock_holder_experiment_id is None


def test_record_skip_bumps_count_and_advances_next_attempt(db_session):
    project = _project(db_session)
    host = _host(db_session, project)
    a = _running_experiment(db_session, project, host, "exp-a")
    next_at = datetime.now(UTC) + timedelta(seconds=60)

    result = lock_service.record_experiment_lock_skip(db_session, a.id, host, next_attempt_at=next_at)
    assert result.skip_count == 1
    # SQLite drops tzinfo on roundtrip; compare the timestamp, not the tzinfo.
    assert result.next_attempt_at.replace(tzinfo=UTC) == next_at
