"""ORM-side validation guard for ``Experiment.phase_owner`` (cleanup follow-up PR1).

Migration 035 deliberately stores ``phase_owner`` as a plain ``String(16)``
(no DB-level CHECK / enum) so the resolver + enum remain the single source
of truth. The trade-off is that any code path that bypasses
``phase_service._sync_phase_owner`` could silently write a garbage string.

``server.domain.models.Experiment._validate_phase_owner`` is the ORM-side
guard that catches such drift at the ORM boundary (before the SQL flush)
and raises ``ValueError`` so the bad value never reaches the DB.

These tests pin down that contract:

* Setting ``phase_owner`` to a valid ``PhaseOwner`` member / value passes.
* Setting ``phase_owner`` to any other string raises ``ValueError``.
* Setting ``phase_owner`` to ``None`` raises ``ValueError`` (the column is
  ``NOT NULL`` at the DB level too, but the ORM guard fires earlier).
* The default value (``server_default="host"``) survives a flush + refresh
  round-trip — the default goes through SQLAlchemy's column default path,
  not ``@validates``, so this confirms we didn't break the default.
"""

from __future__ import annotations

import uuid

import pytest
from map_types.enums import AgentRole, ExperimentPhase, PhaseOwner
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment


def _make_agent(db: Session, *, project_id: uuid.UUID, name: str) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="test-hash",
        api_token_prefix="test",
        role=AgentRole.agent,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_experiment(db: Session, *, project_id: uuid.UUID, creator_id: uuid.UUID) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="phase_owner-orm-guard test",
        description=None,
        phase=ExperimentPhase.draft,
    )
    exp.phase_owner = PhaseOwner.host.value
    db.add(exp)
    db.flush()
    return exp


def test_phase_owner_accepts_enum_member(db_session, project) -> None:
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    exp.phase_owner = PhaseOwner.reviewer
    db_session.commit()
    db_session.refresh(exp)
    assert exp.phase_owner == "reviewer"


def test_phase_owner_accepts_enum_value_string(db_session, project) -> None:
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    exp.phase_owner = "participant"
    db_session.commit()
    db_session.refresh(exp)
    assert exp.phase_owner == "participant"


def test_phase_owner_rejects_unknown_string(db_session, project) -> None:
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    with pytest.raises(ValueError, match="must be a PhaseOwner member"):
        exp.phase_owner = "garbage"


def test_phase_owner_rejects_none(db_session, project) -> None:
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = _make_experiment(db_session, project_id=project_id, creator_id=creator.id)
    with pytest.raises(ValueError, match="must be a PhaseOwner member"):
        exp.phase_owner = None


def test_phase_owner_default_survives_flush(db_session, project) -> None:
    """``server_default='host'`` goes through SQLAlchemy's column default
    path (not ``@validates``); confirm we didn't break the default for
    rows created without an explicit ``phase_owner`` assignment.
    """
    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator.id,
        title="phase_owner-default test",
        description=None,
        phase=ExperimentPhase.draft,
    )
    db_session.add(exp)
    db_session.commit()
    db_session.refresh(exp)
    assert exp.phase_owner == PhaseOwner.host.value


def test_phase_owner_db_not_null_still_holds(db_session, project) -> None:
    """Even if someone bypasses the ORM guard (e.g. via raw SQL), the DB
    ``NOT NULL`` constraint must still reject ``NULL``. Use a raw insert
    to simulate the bypass — confirms defense-in-depth.
    """
    from sqlalchemy import text

    project_id = uuid.UUID(project["id"])
    creator = _make_agent(db_session, project_id=project_id, name="creator")
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO experiments "
                "(id, project_id, creator_agent_id, title, "
                " phase, current_plan_version, lock_skip_count, phase_owner) "
                "VALUES "
                "(:eid, :pid, :aid, 'null-test', 'draft', 1, 0, NULL)"
            ),
            {"eid": str(uuid.uuid4()), "pid": str(project_id), "aid": str(creator.id)},
        )
        db_session.commit()
