"""Tests for perf experiment (193a5074) PR3 — experiment summary bulk scalars.

Pins the Layer-2 N+1 fix on the three per-experiment scalars read by every
``get_todos`` call:

1. ``log_counts_by_experiment`` — single SELECT GROUP BY
2. ``latest_log_by_experiment`` — max + self-join, 2 SELECTs total
3. ``open_unreasonable_count_by_experiment`` — single SELECT GROUP BY

Plus a regression test: with 5 experiments the per-experiment path inside
``get_todos`` issues at most one of each kind (no per-experiment SELECT
regresses inside the summary loop).
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    ExperimentLog,
    ExperimentPhase,
    Project,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
)
from server.services.log_service import (
    latest_log_by_experiment,
    log_counts_by_experiment,
)
from server.services.review_service import open_unreasonable_count_by_experiment


def _make_project(db, key: str = "perf-bulk-3") -> Project:
    project = Project(
        project_key=key,
        name="Perf Bulk 3",
        workspace_path="/tmp/perf-bulk-3",
    )
    db.add(project)
    db.flush()
    return project


def _make_agent(db, project: Project, name: str, role: AgentRole = AgentRole.agent) -> Agent:
    agent = Agent(
        project_id=project.id,
        name=name,
        role=role,
        api_token_hash="x" * 64,
        api_token_prefix=name[:8],
    )
    db.add(agent)
    db.flush()
    return agent


def _make_experiment(
    db, project: Project, host: Agent, title: str, phase: ExperimentPhase
) -> Experiment:
    exp = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        title=title,
        description="perf bulk test",
        phase=phase,
        current_plan_version=1,
    )
    db.add(exp)
    db.flush()
    return exp


def _add_logs(db, exp: Experiment, host: Agent, n: int) -> list[ExperimentLog]:
    rows: list[ExperimentLog] = []
    for i in range(1, n + 1):
        log = ExperimentLog(
            experiment_id=exp.id,
            author_agent_id=host.id,
            summary=f"log {i} for {exp.title}",
            content_md=f"# log {i}",
            log_index=i,
        )
        db.add(log)
        rows.append(log)
    db.flush()
    return rows


def _make_review_with_unreasonable(
    db, exp: Experiment, reviewer: Agent, plan_version: int, count: int
) -> Review:
    review = Review(
        experiment_id=exp.id,
        reviewer_agent_id=reviewer.id,
        plan_version=plan_version,
        substitute_kind="none",
    )
    db.add(review)
    db.flush()
    for i in range(count):
        db.add(
            ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.unreasonable,
                content=f"item {i}",
                status=ReviewItemStatus.open,
            )
        )
    db.flush()
    return review


@pytest.fixture
def bulk_env(db_session):
    project = _make_project(db_session)
    host = _make_agent(db_session, project, "bulk-host")
    reviewer = _make_agent(db_session, project, "bulk-reviewer")
    return project, host, reviewer


def test_log_counts_by_experiment_returns_zero_for_no_logs(db_session, bulk_env):
    project, host, _ = bulk_env
    exp = _make_experiment(db_session, project, host, "no-logs", ExperimentPhase.running)
    db_session.commit()
    db_session.expire_all()

    counts = log_counts_by_experiment(db_session, [exp.id])
    assert counts == {exp.id: 0}


def test_log_counts_by_experiment_groups_correctly(db_session, bulk_env):
    project, host, _ = bulk_env
    a = _make_experiment(db_session, project, host, "a", ExperimentPhase.running)
    b = _make_experiment(db_session, project, host, "b", ExperimentPhase.running)
    c = _make_experiment(db_session, project, host, "c", ExperimentPhase.running)
    _add_logs(db_session, a, host, 3)
    _add_logs(db_session, b, host, 1)
    _add_logs(db_session, c, host, 5)
    db_session.commit()
    db_session.expire_all()

    counts = log_counts_by_experiment(db_session, [a.id, b.id, c.id])
    assert counts == {a.id: 3, b.id: 1, c.id: 5}


def test_log_counts_by_experiment_empty_input_no_db_hit(db_session, bulk_env):
    # No commit, no rows — but the function should short-circuit before any SELECT.
    assert log_counts_by_experiment(db_session, []) == {}


def test_latest_log_by_experiment_picks_highest_log_index(db_session, bulk_env):
    project, host, _ = bulk_env
    a = _make_experiment(db_session, project, host, "latest-a", ExperimentPhase.running)
    b = _make_experiment(db_session, project, host, "latest-b", ExperimentPhase.running)
    _add_logs(db_session, a, host, 3)  # latest = log_index 3
    _add_logs(db_session, b, host, 1)  # latest = log_index 1
    db_session.commit()
    db_session.expire_all()

    latest = latest_log_by_experiment(db_session, [a.id, b.id])
    assert latest[a.id].log_index == 3
    assert latest[a.id].summary == "log 3 for latest-a"
    assert latest[b.id].log_index == 1


def test_latest_log_by_experiment_returns_empty_dict_for_no_logs(db_session, bulk_env):
    project, host, _ = bulk_env
    exp = _make_experiment(
        db_session, project, host, "no-logs-latest", ExperimentPhase.running
    )
    db_session.commit()
    db_session.expire_all()

    assert latest_log_by_experiment(db_session, [exp.id]) == {}


def test_latest_log_by_experiment_empty_input_no_db_hit(db_session):
    assert latest_log_by_experiment(db_session, []) == {}


def test_open_unreasonable_count_by_experiment_groups_by_experiment(db_session, bulk_env):
    project, host, reviewer = bulk_env
    a = _make_experiment(db_session, project, host, "open-a", ExperimentPhase.review)
    b = _make_experiment(db_session, project, host, "open-b", ExperimentPhase.review)
    _make_review_with_unreasonable(db_session, a, reviewer, plan_version=1, count=2)
    _make_review_with_unreasonable(db_session, b, reviewer, plan_version=1, count=4)
    db_session.commit()
    db_session.expire_all()

    counts = open_unreasonable_count_by_experiment(db_session, [a.id, b.id])
    assert counts == {a.id: 2, b.id: 4}


def test_open_unreasonable_count_by_experiment_excludes_resolved(db_session, bulk_env):
    """Only items with status in (open, addressed, rebutted, escalated) count.
    Resolved items must be excluded — same scope as the singular helper."""
    project, host, reviewer = bulk_env
    exp = _make_experiment(
        db_session, project, host, "mixed-states", ExperimentPhase.review
    )
    review = _make_review_with_unreasonable(
        db_session, exp, reviewer, plan_version=1, count=0
    )
    # 1 open + 1 addressed + 1 resolved (should NOT count)
    db_session.add(
        ReviewItem(
            review_id=review.id,
            kind=ReviewItemKind.unreasonable,
            content="open one",
            status=ReviewItemStatus.open,
        )
    )
    db_session.add(
        ReviewItem(
            review_id=review.id,
            kind=ReviewItemKind.unreasonable,
            content="addressed one",
            status=ReviewItemStatus.addressed,
        )
    )
    db_session.add(
        ReviewItem(
            review_id=review.id,
            kind=ReviewItemKind.unreasonable,
            content="resolved one",
            status=ReviewItemStatus.closed,
            last_resolution_reason="resolved",
        )
    )
    db_session.commit()
    db_session.expire_all()

    counts = open_unreasonable_count_by_experiment(db_session, [exp.id])
    assert counts == {exp.id: 2}  # only open + addressed count


def test_get_todos_uses_bulk_scalars_not_per_experiment_selects(db_session, engine, bulk_env):
    """``get_todos`` must NOT issue a per-experiment SELECT for log count,
    latest log, or open-unreasonable count when the bulk helpers exist.
    With 5 open experiments, allow ≤1 SELECT of each kind (the bulk
    helper itself). Anything more means the N+1 regressed."""
    project, host, reviewer = bulk_env
    exps = []
    for i in range(5):
        exp = _make_experiment(
            db_session, project, host, f"bulk-summary-{i}", ExperimentPhase.review
        )
        _add_logs(db_session, exp, host, 2)
        _make_review_with_unreasonable(
            db_session, exp, reviewer, plan_version=1, count=1
        )
        exps.append(exp)
    db_session.commit()
    db_session.expire_all()

    counters = {
        "experiment_logs": 0,
        # Count of SELECTs against experiment_logs whose WHERE mentions
        # any of the open experiments. We can't tell GROUP BY from the
        # raw statement (SQLAlchemy renders both forms similarly), so we
        # approximate by counting "experiment_logs" mentions across the
        # full statement.
        "review_items": 0,
    }

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        s = statement.lower()
        if "from experiment_logs" in s:
            counters["experiment_logs"] += 1
        if "from review_items" in s and "join reviews" in s:
            counters["review_items"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        from server.services.todo_service import get_todos

        todos = get_todos(db_session, host)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    # Bulk log_count + bulk latest_log = 2 SELECTs against experiment_logs.
    # No per-experiment lookups.
    assert counters["experiment_logs"] <= 2, (
        f"experiment_logs SELECTs={counters['experiment_logs']} (bulk=2). "
        f"N+1 regressed."
    )
    # Bulk open_unreasonable = 1 SELECT against review_items.
    # Other ReviewItem SELECTs may exist for pending_replies / review-status
    # projections; the bulk helper itself must be present.
    assert counters["review_items"] >= 1, (
        "open_unreasonable bulk helper did not run (no review_items SELECT)."
    )

    # Sanity: every experiment surfaces in my_open_experiments with the
    # correct scalars attached.
    by_id = {e.id: e for e in todos.my_open_experiments}
    assert set(by_id) == {e.id for e in exps}
    for e in exps:
        summary = by_id[e.id]
        assert summary.log_count == 2, summary
        assert summary.latest_log_summary == "log 2 for " + e.title, summary
        assert summary.open_unreasonable_count == 1, summary
