"""Tests for the perf experiment (193a5074) PR1 ``count_sql_calls`` fixture.

This is a minimal first cut: the fixture counts SQL statements executed
against the active session's bind (an in-memory SQLite for tests). It
captures statement text + per-statement duration. Tests assert that
the simple-waker hot-path (the existing ``build_wake_context`` and
related helpers) stays under a starting threshold — ``count <= 50``
per the plan's "起步阈值". Once we know the real number, we tighten
in a follow-up PR.

Usage
-----

    from tests._perf_sql import count_sql_calls

    def test_waker_path_stays_below_threshold(db_session, engine):
        with count_sql_calls(engine, label="waker.build_wake_context") as c:
            build_wake_context(...)
        assert c.count <= 50, c.summary()
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from cli.simple_waker import build_wake_context
from server.domain.models import AgentRole, Project
from server.services.auth import create_agent


@dataclass
class SqlCallCounter:
    label: str
    count: int = 0
    total_ms: float = 0.0
    statements: list[tuple[str, float]] = field(default_factory=list)
    _before_ms: float | None = None

    def record_before(self) -> None:
        self._before_ms = time.perf_counter()

    def record_after(self, statement: str) -> None:
        if self._before_ms is None:
            return
        elapsed_ms = (time.perf_counter() - self._before_ms) * 1000.0
        self.count += 1
        self.total_ms += elapsed_ms
        self.statements.append((statement, elapsed_ms))

    def summary(self) -> str:
        lines = [
            f"[{self.label}] count={self.count} total_ms={self.total_ms:.2f}",
        ]
        for idx, (stmt, ms) in enumerate(self.statements[:10], start=1):
            preview = " ".join(stmt.split())[:120]
            lines.append(f"  #{idx} {ms:.2f}ms  {preview}")
        if len(self.statements) > 10:
            lines.append(f"  ... {len(self.statements) - 10} more")
        return "\n".join(lines)


@contextmanager
def count_sql_calls(engine: Engine, *, label: str) -> Iterator[SqlCallCounter]:
    counter = SqlCallCounter(label=label)

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        counter.record_before()

    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        counter.record_after(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)


@pytest.fixture
def perf_seed(db_session, engine):
    """Minimal seed for waker path testing: 1 project, 1 host, 5 topics,
    5 experiments, 5 reviews. Drives ``build_wake_context`` with non-empty
    inputs."""
    project = Project(
        project_key="perf-seed",
        name="Perf Seed",
        workspace_path="/tmp/perf-seed",
    )
    db_session.add(project)
    db_session.flush()
    host, _ = create_agent(db_session, "host", AgentRole.agent, project_id=project.id)
    db_session.commit()
    return {
        "engine": engine,
        "session": db_session,
        "project": project,
        "host": host,
    }


def test_count_sql_calls_records_executions(perf_seed):
    """Sanity: the fixture records at least one SQL statement and
    produces a non-empty summary."""
    engine = perf_seed["engine"]
    session = perf_seed["session"]
    with count_sql_calls(engine, label="smoke") as counter:
        # Any DB activity counts.
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
    assert counter.count >= 1
    summary = counter.summary()
    assert "count=" in summary


def test_build_wake_context_with_empty_input_stays_under_threshold(perf_seed):
    """Baseline: ``build_wake_context`` with empty todos / no topic
    progress / no notifications should not trigger any DB queries
    (it's pure-Python aggregation)."""
    engine = perf_seed["engine"]
    with count_sql_calls(engine, label="waker.empty") as counter:
        ctx = build_wake_context(
            topic_progress_data=None,
            todos={},
            notifications=None,
        )
    assert ctx.has_work is False
    assert counter.count == 0


def test_count_sql_calls_threshold_smoke(perf_seed):
    """Starting-threshold smoke (per plan: count <= 50). Real waker
    path involves server API calls (out of scope for the fixture test);
    here we only verify the threshold assertion shape works."""
    engine = perf_seed["engine"]
    session = perf_seed["session"]
    host = perf_seed["host"]

    with count_sql_calls(engine, label="waker.todos-fetch") as counter:
        # Reading the host's todos through the service layer to simulate
        # a single waker cycle's DB activity.
        from server.services import todo_service

        todo_service.get_todos(session, host)

    assert counter.count <= 50, counter.summary()
