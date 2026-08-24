"""review archived metadata + plan_revise auto-archive (experiment 18f1d8f6 I1(a)).

Pins plan (a) acceptance:

1. Migration 034 adds ``reviews.archived_at`` + ``reviews.archived_reason``;
   columns are nullable; an ``ix_reviews_archived_at`` partial index targets
   ``archived_at IS NULL`` (the hot path for active reviews).
2. Pre-existing rows are backfilled with ``archived_reason='auto'`` and
   ``archived_at=NULL`` so the UI can render a
   ``(pre-archive, all reviews shown)`` hint during the N=2 minor-version
   transition. They are still "active" from the API/filter perspective
   because the canonical signal is ``archived_at``.
3. The new fields are round-tripped through ``ReviewRead`` ORM schema.
4. ``ReviewArchivedReason`` enum exposes the 3 documented values.
5. Alembic 034 is reversible (downgrade drops both columns + index).
6. Migration is idempotent: re-running upgrade is a no-op.

The I1(b) auto-archive trigger (plan_revise marks prior reviews as
``archived_reason='auto' + archived_at=now()``) lives in its own test
module because it requires a follow-up change to ``revise_plan``.
"""

from __future__ import annotations

import os
import uuid

from alembic.config import Config as AlembicConfig
from map_types import ReviewArchivedReason
from map_types.enums import ReviewSubstituteKind
from map_types.schemas import ReviewRead
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alembic import command
from server.domain.models import Review


def _make_alembic_config(db_url: str) -> AlembicConfig:
    """alembic env reads URL from ``get_settings()`` (lru_cache'd) — set the env
    var and clear the cache so the migration targets the test DB.
    """
    from server.config import get_settings

    os.environ["MAP_DATABASE_URL"] = db_url
    get_settings.cache_clear()
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _baseline_schema_at_033(engine, db_url: str) -> None:
    """Apply every alembic migration up to (but not including) 034, leaving
    the database in the exact pre-I1(a) state.
    """
    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "033")


# ---------------------------------------------------------------------------
# Migration 034: column shape + backfill semantics
# ---------------------------------------------------------------------------


def test_migration_034_adds_archived_columns(tmp_path):
    """Migration 034 adds archived_at + archived_reason + ix_reviews_archived_at."""
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )
    _baseline_schema_at_033(engine, db_url)

    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "034")

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("reviews")}
    assert "archived_at" in cols
    assert cols["archived_at"]["nullable"] is True
    assert "archived_reason" in cols
    assert cols["archived_reason"]["nullable"] is True

    indexes = insp.get_indexes("reviews")
    archived_ix = [ix for ix in indexes if ix["name"] == "ix_reviews_archived_at"]
    assert archived_ix, f"missing ix_reviews_archived_at; got {indexes}"
    assert archived_ix[0]["column_names"] == ["archived_at"]


def test_migration_034_backfills_existing_reviews_with_auto_reason(tmp_path):
    """Pre-existing rows are backfilled with archived_reason='auto', archived_at=NULL."""
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )
    _baseline_schema_at_033(engine, db_url)

    legacy_review_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reviews (id, experiment_id, reviewer_agent_id, plan_version, substitute_kind) "
                "VALUES (:id, :eid, :rid, :pv, 'none')"
            ),
            {
                "id": str(legacy_review_id),
                "eid": str(uuid.uuid4()),
                "rid": str(uuid.uuid4()),
                "pv": 1,
            },
        )

    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "034")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT archived_at, archived_reason FROM reviews WHERE id = :id"),
            {"id": str(legacy_review_id)},
        ).one()
    assert row.archived_at is None
    assert row.archived_reason == "auto"


def test_migration_034_is_idempotent(tmp_path):
    """Re-running upgrade head is a no-op — the migration guards with _has_column."""
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )
    _baseline_schema_at_033(engine, db_url)

    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "034")
    # Second upgrade head should not error.
    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "head")


def test_migration_034_is_reversible(tmp_path):
    """downgrade drops both columns and the index."""
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )
    _baseline_schema_at_033(engine, db_url)

    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.upgrade(cfg, "034")

    cfg = _make_alembic_config(db_url)
    cfg.attributes["connection"] = engine
    command.downgrade(cfg, "033")

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("reviews")}
    assert "archived_at" not in cols
    assert "archived_reason" not in cols
    indexes = insp.get_indexes("reviews")
    assert not any(ix["name"] == "ix_reviews_archived_at" for ix in indexes)


# ---------------------------------------------------------------------------
# Schema contract: ReviewRead + ReviewArchivedReason
# ---------------------------------------------------------------------------


def test_review_read_schema_round_trips_archived_fields():
    """ReviewRead serialises/deserialises archived_at + archived_reason."""
    payload = {
        "id": str(uuid.uuid4()),
        "experiment_id": str(uuid.uuid4()),
        "reviewer_agent_id": str(uuid.uuid4()),
        "plan_version": 1,
        "substitute_kind": "none",
        "created_at": "2026-07-08T04:00:00Z",
        "archived_at": None,
        "archived_reason": None,
        "items": [],
    }
    read = ReviewRead.model_validate(payload)
    assert read.archived_at is None
    assert read.archived_reason is None

    payload2 = dict(payload, archived_at="2026-07-08T04:30:00Z", archived_reason="auto")
    read2 = ReviewRead.model_validate(payload2)
    assert read2.archived_at is not None
    assert read2.archived_reason == ReviewArchivedReason.auto


def test_review_archived_reason_enum_values():
    """ReviewArchivedReason exposes exactly the 3 documented values."""
    assert {r.value for r in ReviewArchivedReason} == {"auto", "manual", "superseded"}


def test_manual_archive_via_orm():
    """Operators can manually archive a review (e.g. duplicate row, withdrawn reviewer).

    Pin the I1(a) acceptance for the ``manual`` reason — the schema
    supports it even though the trigger flow uses ``auto``. Use a fresh
    in-memory DB so the ORM write exercises the real column shape without
    depending on migration state.
    """
    from datetime import datetime

    eng = create_engine("sqlite:///:memory:")
    Review.__table__.create(eng)
    with Session(eng) as db:
        review = Review(
            id=uuid.uuid4(),
            experiment_id=uuid.uuid4(),
            reviewer_agent_id=uuid.uuid4(),
            plan_version=1,
            substitute_kind=ReviewSubstituteKind.none,
            archived_at=datetime.utcnow(),
            archived_reason=ReviewArchivedReason.manual,
        )
        db.add(review)
        db.commit()
        db.refresh(review)
        assert review.archived_reason == ReviewArchivedReason.manual
        assert review.archived_at is not None
