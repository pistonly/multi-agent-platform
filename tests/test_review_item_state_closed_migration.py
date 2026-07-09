"""review_item.state 终态统一 closed + last_resolution_reason (experiment b95894db I1(c)).

Pins plan (c) acceptance:

1. Migration 032 collapses historical ``resolved`` rows into the new
   ``closed`` terminal with ``last_resolution_reason='resolved'``.
2. PATCH ``status=resolved`` is normalised at the API layer to
   ``status=closed, last_resolution_reason=resolved``.
3. PATCH ``status=withdrawn`` is normalised to
   ``status=closed, last_resolution_reason=superseded``.
4. ``status=rebutted`` is NOT collapsed to ``closed`` because it is a
   mid-cycle signal — the reviewer can still flip it back to ``resolved``
   or ``open`` (the rebutted flow stays a real two-step handshake).
5. ``can_approve`` and ``_prior_version_reviews_fully_resolved`` accept
   the new ``closed`` terminal in addition to legacy ``resolved``.
6. Alembic 032 is reversible (downgrade restores ``resolved``).
"""

from __future__ import annotations

import uuid

import pytest
from map_types.enums import ResolutionReason, ReviewItemStatus
from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config as AlembicConfig
from server.domain.state_machine import (
    ReviewItemTransitionContext,
    validate_review_item_transition,
)


def _make_alembic_config(db_url: str) -> AlembicConfig:
    # ``alembic/env.py`` re-reads the URL from ``get_settings()`` (which is
    # ``lru_cache``-ed). Set the env var *and* clear the cache so the
    # migrations target the right database.
    import os

    from server.config import get_settings

    os.environ["MAP_DATABASE_URL"] = db_url
    get_settings.cache_clear()
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _baseline_schema_at_031(engine, db_url: str) -> None:
    """Run every alembic migration up to (but not including) 032, leaving
    the database in the exact pre-I1(c) state.

    The tmp_path fixture provides a fresh database file per test, so alembic
    can create the schema from scratch via the migrations alone (no
    ``Base.metadata.create_all`` short-circuit that would mask what 032
    actually adds).
    """
    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "031")


def test_migration_032_collapses_legacy_resolved_into_closed_with_reason(tmp_path):
    # Use a file-backed SQLite so the alembic connection and the test session
    # share the same database (in-memory SQLite would isolate them per
    # connection).
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )
    _baseline_schema_at_031(engine, db_url)

    # Use raw SQL inserts so the ORM doesn't try to write columns that
    # 032 hasn't introduced yet (``last_resolution_reason``).
    from sqlalchemy import text

    legacy_item_id = uuid.uuid4()
    review_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reviews (id, experiment_id, reviewer_agent_id, plan_version, substitute_kind) "
                "VALUES (:id, :eid, :rid, :pv, 'none')"
            ),
            {
                "id": str(review_id),
                "eid": str(uuid.uuid4()),
                "rid": str(uuid.uuid4()),
                "pv": 1,
            },
        )
        conn.execute(
            text(
                "INSERT INTO review_items (id, review_id, kind, content, status) "
                "VALUES (:id, :rid, 'unreasonable', :content, 'resolved')"
            ),
            {
                "id": str(legacy_item_id),
                "rid": str(review_id),
                "content": "legacy resolved",
            },
        )

    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "032")

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("review_items")}
    assert "last_resolution_reason" in cols

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, last_resolution_reason FROM review_items WHERE id = :id"
            ),
            {"id": str(legacy_item_id)},
        ).one()
    assert row[0] == "closed"
    assert row[1] == "resolved"


def test_migration_032_is_reversible(tmp_path):
    db_path = tmp_path / "migration_reversible.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )
    _baseline_schema_at_031(engine, db_url)

    from sqlalchemy import text

    item_id = uuid.uuid4()
    review_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reviews (id, experiment_id, reviewer_agent_id, plan_version, substitute_kind) "
                "VALUES (:id, :eid, :rid, :pv, 'none')"
            ),
            {
                "id": str(review_id),
                "eid": str(uuid.uuid4()),
                "rid": str(uuid.uuid4()),
                "pv": 1,
            },
        )
        conn.execute(
            text(
                "INSERT INTO review_items (id, review_id, kind, content, status) "
                "VALUES (:id, :rid, 'unreasonable', 'legacy', 'resolved')"
            ),
            {"id": str(item_id), "rid": str(review_id)},
        )

    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "032")
    command.downgrade(cfg, "031")

    insp = inspect(engine)
    assert "last_resolution_reason" not in {
        c["name"] for c in insp.get_columns("review_items")
    }
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM review_items WHERE id = :id"),
            {"id": str(item_id)},
        ).one()
    assert row[0] == "resolved"


def test_state_machine_transition_table_keeps_legacy_paths():
    """Legacy transition table entries remain valid at the state-machine
    layer — the API layer normalises on the way to the database."""
    ctx_reviewer = ReviewItemTransitionContext(
        is_creator=False, is_reviewer=True, is_admin=False
    )
    # addressed → resolved is the canonical reviewer-accepts path that the
    # normalisation shim rewrites to ``closed{resolved}``.
    validate_review_item_transition(
        ReviewItemStatus.addressed,
        ReviewItemStatus.resolved,
        ctx_reviewer,
    )
    # And the rebuttal follow-up path stays intact:
    validate_review_item_transition(
        ReviewItemStatus.rebutted,
        ReviewItemStatus.resolved,
        ctx_reviewer,
    )


def test_resolution_reason_enum_exposes_documented_values():
    """``ResolutionReason`` must contain exactly the values documented in the
    plan: resolved, rebutted, superseded. The ``archived`` reason is owned
    by experiment 18f1d8f6 and intentionally absent here."""
    actual = {r.value for r in ResolutionReason}
    assert actual == {"resolved", "rebutted", "superseded"}


def test_review_item_status_enum_includes_closed_terminal():
    """``ReviewItemStatus.closed`` is the new single terminal value."""
    assert ReviewItemStatus.closed.value == "closed"


@pytest.mark.parametrize(
    ("legacy_status", "expected_status", "expected_reason"),
    [
        (ReviewItemStatus.resolved, ReviewItemStatus.closed, ResolutionReason.resolved),
        (ReviewItemStatus.withdrawn, ReviewItemStatus.closed, ResolutionReason.superseded),
    ],
)
def test_normalize_terminal_status_collapses_legacy_terminals(
    legacy_status, expected_status, expected_reason
):
    from server.services.review_service import _normalize_terminal_status

    new_status, reason = _normalize_terminal_status(legacy_status)
    assert new_status == expected_status
    assert reason == expected_reason


def test_normalize_terminal_status_preserves_rebutted_as_mid_cycle_signal():
    """``rebutted`` must NOT be collapsed to ``closed`` because it is a
    mid-cycle signal that the reviewer can still flip to ``resolved`` or
    ``open``. Collapsing it would break the rebuttal follow-up."""
    from server.services.review_service import _normalize_terminal_status

    new_status, reason = _normalize_terminal_status(ReviewItemStatus.rebutted)
    assert new_status == ReviewItemStatus.rebutted
    assert reason is None


def test_normalize_terminal_status_passes_through_open_states():
    """Non-terminal transitions are untouched by the normalisation shim."""
    from server.services.review_service import _normalize_terminal_status

    for status in (
        ReviewItemStatus.open,
        ReviewItemStatus.addressed,
        ReviewItemStatus.escalated,
        ReviewItemStatus.closed,
    ):
        new_status, reason = _normalize_terminal_status(status)
        if status == ReviewItemStatus.closed:
            # ``closed`` is itself the canonical terminal — no reason rewrite.
            assert new_status == ReviewItemStatus.closed
            assert reason is None
        else:
            assert new_status == status
            assert reason is None
