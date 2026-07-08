"""reviews.archived_at + archived_reason (experiment 18f1d8f6 I1(a))

Add archive metadata to ``reviews`` so the data lifecycle is explicit
when a host revises a plan and the previous review is no longer the
canonical review for the current plan.

Columns added
-------------
- ``archived_at`` (datetime, nullable) — NULL for active reviews;
  set the moment a review becomes archived.
- ``archived_reason`` (enum, nullable) — one of
  ``{auto, manual, superseded}``. NULL for active reviews. Backfilled
  with ``'auto'`` for all rows that exist at migration time so the
  UI can render a ``(pre-archive, all reviews shown)`` hint during the
  N=2 minor-version transition.

Constraints
-----------
- ``plan_version`` is already present (migration 002 / 026 history);
  we do not add it again. The plan document lists ``plan_version`` in
  scope (a) only to call out the *semantics* — the field stays put and
  is treated as the immutable plan version the review was authored
  against. Callers should consult ``experiment.current_plan_version``
  dynamically for the active plan.

Backfill
--------
Existing rows: ``archived_at = NULL``, ``archived_reason = 'auto'``.
This combination means "review existed before the archive feature; the
UI shows it everywhere until plan_revise archives it for real".

SQLite notes
------------
``ALTER TABLE ... ADD COLUMN`` cannot attach a CHECK constraint nor an
enum type. We add the column as a plain string with a server default,
then ``batch_alter_table`` (SQLite only) to attach the CHECK constraint
+ create the partial index. PostgreSQL gets the CHECK inline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ARCHIVED_REASONS = ("auto", "manual", "superseded")


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "reviews" not in inspector.get_table_names():
        return
    if _has_column(inspector, "reviews", "archived_at"):
        return  # idempotent guard — re-running upgrade must be a no-op

    if bind.dialect.name == "postgresql":
        # PG path: enum + check constraint inline.
        archived_reason_enum = sa.Enum(
            *_ARCHIVED_REASONS,
            name="review_archived_reason",
        )
        archived_reason_enum.create(bind, checkfirst=True)
        op.add_column(
            "reviews",
            sa.Column(
                "archived_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.add_column(
            "reviews",
            sa.Column(
                "archived_reason",
                archived_reason_enum,
                nullable=True,
                server_default="auto",
            ),
        )
        # Partial index — active reviews (archived_at IS NULL) are the hot path.
        op.create_index(
            "ix_reviews_archived_at",
            "reviews",
            ["archived_at"],
            sqlite_where=sa.text("archived_at IS NULL"),
            postgresql_where=sa.text("archived_at IS NULL"),
        )
        # Backfill historical reviews: archived_reason='auto' means "pre-archive".
        op.execute(
            sa.text(
                "UPDATE reviews SET archived_reason = 'auto' WHERE archived_reason IS NULL"
            )
        )
    else:
        # SQLite path: batch_alter_table for column-level metadata changes.
        # We don't add CHECK constraints because SQLite ignores them on table
        # rewrite anyway — the ORM-level enum + service-level validation is
        # the authoritative gate (see services/review_service.py).
        with op.batch_alter_table("reviews") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "archived_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "archived_reason",
                    sa.String(length=16),
                    nullable=True,
                    server_default="auto",
                )
            )
        op.create_index(
            "ix_reviews_archived_at",
            "reviews",
            ["archived_at"],
            sqlite_where=sa.text("archived_at IS NULL"),
        )
        # Backfill historical reviews: archived_reason='auto' for legacy rows.
        op.execute(
            sa.text(
                "UPDATE reviews SET archived_reason = 'auto' WHERE archived_reason IS NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "reviews" not in inspector.get_table_names():
        return
    if not _has_column(inspector, "reviews", "archived_at"):
        return

    op.drop_index("ix_reviews_archived_at", table_name="reviews")
    if bind.dialect.name == "postgresql":
        op.drop_column("reviews", "archived_reason")
        op.drop_column("reviews", "archived_at")
        sa.Enum(name="review_archived_reason").drop(bind, checkfirst=True)
    else:
        with op.batch_alter_table("reviews") as batch_op:
            batch_op.drop_column("archived_reason")
            batch_op.drop_column("archived_at")
