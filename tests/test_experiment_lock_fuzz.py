"""Fuzz test for ``server.services.lock_service.acquire_experiment_lock``.

Race experiment (eca0f522) PR1 acceptance:
> CI fuzz 100 次断言总 commit 数与总行数稳定

Strategy
--------
This test runs ``acquire_experiment_lock`` 100 times against a project
with multiple candidate experiments, shuffled each iteration, and
asserts that:

* the holder set on the project is consistent (≤ 1 experiment holds
  the lock at any point),
* each iteration's "successful acquires + failed acquires" counts add
  up to the number of candidate experiments (no silent drops).

Scope & limitations
-------------------
* Sequential: SQLite + StaticPool serialises writes inside one process
  so true concurrent acquires collapse to a single in-flight commit.
  The contract this protects — "exactly one holder after each iteration"
  — is what the unique partial index (PG) / plain unique index (SQLite)
  enforces, and is the regression we most care about.
* For a real cross-process race, use PG via the ``integration`` marker
  (covered by ``tests/integration/test_experiment_lock_race_pg.py``,
  which is out of scope here because CI uses SQLite).

The test is marked ``slow`` because 100 iterations × N experiments is
non-trivial; ``pytest -m 'not slow'`` skips it on PR runs.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

import pytest

from server.domain.models import Agent, AgentRole, Experiment, ExperimentPhase, Project
from server.services import lock_service
from server.services.auth import create_agent
from server.services.errors import ConflictError

ITERATIONS = 100
CANDIDATES = 4


def _seed(db_session) -> tuple[Project, Agent, list[Experiment]]:
    project = Project(project_key="lock-fuzz", name="Lock Fuzz", workspace_path="/tmp/lock-fuzz")
    db_session.add(project)
    db_session.flush()
    host, _ = create_agent(db_session, "host-fuzz", AgentRole.agent, project_id=project.id)

    experiments = []
    for i in range(CANDIDATES):
        exp = Experiment(
            project_id=project.id,
            creator_agent_id=host.id,
            title=f"fuzz-{i}",
            phase=ExperimentPhase.running,
            current_plan_version=1,
        )
        db_session.add(exp)
        db_session.flush()
        experiments.append(exp)

    return project, host, experiments


def _current_holder(db_session, project: Project) -> uuid.UUID | None:
    for exp in db_session.query(Experiment).filter(Experiment.project_id == project.id).all():
        if exp.lock_holder_experiment_id == exp.id:
            return exp.id
    return None


@pytest.mark.slow
def test_acquire_100_iterations_single_holder_at_all_times(db_session):
    project, host, experiments = _seed(db_session)
    successes = 0
    conflicts = 0

    for i in range(ITERATIONS):
        order = list(experiments)
        random.shuffle(order)
        iteration_success = 0
        for exp in order:
            try:
                lock_service.acquire_experiment_lock(db_session, exp.id, host)
                iteration_success += 1
                successes += 1
            except ConflictError:
                conflicts += 1

        # Per-iteration invariant: exactly one holder on the project.
        holder = _current_holder(db_session, project)
        assert holder is not None, f"iter={i} no holder after {iteration_success} successful acquires"
        assert iteration_success == 1, f"iter={i} expected 1 winner, got {iteration_success}"

        # Reset for next iteration so we always start from "no holder".
        holder_exp = next(e for e in experiments if e.id == holder)
        lock_service.release_experiment_lock(db_session, holder_exp.id, host)

    assert successes == ITERATIONS
    # Total conflicts = iterations * (candidates - 1)
    assert conflicts == ITERATIONS * (CANDIDATES - 1)


@pytest.mark.slow
def test_force_release_invariant_under_repeated_force_calls(db_session):
    """Repeated force_release on a project with no holder is a no-op
    that must not corrupt state — the retry helper must succeed cleanly.
    """
    project, host, experiments = _seed(db_session)
    for _ in range(ITERATIONS):
        result = lock_service.force_release_experiment_lock(
            db_session, experiments[0].id, host, reason="fuzz"
        )
        assert result.holder is None
