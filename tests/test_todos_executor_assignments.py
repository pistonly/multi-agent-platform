"""plan-mode-direct-execution-productization I1: executor_assignments todo
partition.

Pin the contract:
- The designated executor (after ``start --executor``) sees a running
  delegated experiment in ``executor_assignments``.
- The host (creator but NOT executor) does NOT see the same experiment
  in ``executor_assignments`` (it stays in ``my_open_experiments``).
- After ``complete``, the experiment drops out of ``executor_assignments``
  because it is no longer in ``running`` phase.
- Archived / deleted experiments never appear in either partition.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Experiment, ExperimentPhase
from server.services import todo_service


def _make_agent(db: Session, *, project_id: uuid.UUID, name: str) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="test-hash",
        api_token_prefix="tst",
        role=AgentRole.agent,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_running_with_executor(
    db: Session,
    *,
    project_id: uuid.UUID,
    creator: Agent,
    executor: Agent,
    title: str = "delegated-running",
) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator.id,
        executor_agent_id=executor.id,
        title=title,
        phase=ExperimentPhase.running,
        current_plan_version=1,
    )
    db.add(exp)
    db.flush()
    return exp


def test_executor_sees_running_assignment(db_session: Session, project: dict) -> None:
    project_id = uuid.UUID(project["id"])
    host = _make_agent(db_session, project_id=project_id, name="host-x")
    executor = _make_agent(db_session, project_id=project_id, name="participant-x")
    _make_running_with_executor(
        db_session,
        project_id=project_id,
        creator=host,
        executor=executor,
    )

    todos = todo_service.get_todos(db_session, executor)
    assert any(e.executor_agent_id == executor.id for e in todos.executor_assignments)
    # Creator (host) is not the executor on this experiment → no entry.
    host_todos = todo_service.get_todos(db_session, host)
    assert all(
        e.executor_agent_id != host.id for e in host_todos.executor_assignments
    )


def test_host_creator_not_in_executor_assignments(db_session: Session, project: dict) -> None:
    """Self-execution carve-out: when ``executor_agent_id == creator_agent_id ==
    agent.id``, the experiment shows up only in ``my_open_experiments``
    (not duplicated in ``executor_assignments``). The carve-out prevents
    the host from receiving the same running experiment in two partitions
    when they self-execute (no actual delegation happened)."""
    project_id = uuid.UUID(project["id"])
    host = _make_agent(db_session, project_id=project_id, name="host-self-exec")
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=host.id,
        executor_agent_id=host.id,  # self-execute (host == executor)
        title="self-execute",
        phase=ExperimentPhase.running,
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()

    host_todos = todo_service.get_todos(db_session, host)
    assert any(e.id == exp.id for e in host_todos.my_open_experiments)
    # Self-execute carve-out: must NOT appear in executor_assignments.
    assert all(e.id != exp.id for e in host_todos.executor_assignments)


def test_executor_assignment_deduped_with_creator_view(db_session: Session, project: dict) -> None:
    """Cross-persona delegation: a delegated experiment is visible to the
    executor (different agent) in ``executor_assignments`` and to the
    creator/host in ``my_open_experiments`` — never both in the same
    partition on the same agent (no duplication anywhere)."""
    project_id = uuid.UUID(project["id"])
    host = _make_agent(db_session, project_id=project_id, name="host-cd")
    executor = _make_agent(db_session, project_id=project_id, name="participant-cd")
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=host.id,
        executor_agent_id=executor.id,
        title="cross-persona-delegation",
        phase=ExperimentPhase.running,
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()

    executor_todos = todo_service.get_todos(db_session, executor)
    host_todos = todo_service.get_todos(db_session, host)

    # Executor sees it in executor_assignments (and is NOT the creator, so not in my_open_experiments).
    assert any(e.id == exp.id for e in executor_todos.executor_assignments)
    assert all(e.id != exp.id for e in executor_todos.my_open_experiments)
    # Host sees it in my_open_experiments and (correctly) NOT in executor_assignments.
    assert any(e.id == exp.id for e in host_todos.my_open_experiments)
    assert all(e.id != exp.id for e in host_todos.executor_assignments)


def test_completed_experiment_drops_out(db_session: Session, project: dict) -> None:
    project_id = uuid.UUID(project["id"])
    host = _make_agent(db_session, project_id=project_id, name="host-c")
    executor = _make_agent(db_session, project_id=project_id, name="participant-c")
    exp = _make_running_with_executor(
        db_session,
        project_id=project_id,
        creator=host,
        executor=executor,
        title="complete-me",
    )
    exp.phase = ExperimentPhase.result_review  # post-complete phase
    db_session.flush()

    todos = todo_service.get_todos(db_session, executor)
    assert all(e.id != exp.id for e in todos.executor_assignments)


def test_archived_excluded_from_executor_assignments(db_session: Session, project: dict) -> None:
    project_id = uuid.UUID(project["id"])
    host = _make_agent(db_session, project_id=project_id, name="host-a")
    executor = _make_agent(db_session, project_id=project_id, name="participant-a")
    exp = _make_running_with_executor(
        db_session,
        project_id=project_id,
        creator=host,
        executor=executor,
        title="archived",
    )
    exp.archived_at = datetime.now(timezone.utc)
    db_session.flush()

    todos = todo_service.get_todos(db_session, executor)
    assert all(e.id != exp.id for e in todos.executor_assignments)
