"""review_items.last_resolution_reason + state=closed 终态统一 (experiment b95894db I1(c))

Revision ID: 032
Revises: 031
Create Date: 2026-07-08

Adds a ``last_resolution_reason`` column to ``review_items`` (nullable),
backfills historical ``resolved`` rows into the new ``closed`` terminal
state, and extends the PostgreSQL ``reviewitemstatus`` enum with the
``closed`` value introduced by I1(c).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_TERMINAL_VALUES = ("resolved", "rebutted", "withdrawn")


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _enum_values(inspector: sa.Inspector, enum_name: str) -> set[str]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        rows = bind.execute(
            sa.text(
                "SELECT unnest(enum_range(NULL::reviewitemstatus))::text AS v"
            )
        ).fetchall()
        return {row[0] for row in rows}
    # SQLite (tests) stores enum values as plain VARCHAR; reflect from the
    # column type when possible.
    return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "review_items" not in inspector.get_table_names():
        return

    if not _has_column(inspector, "review_items", "last_resolution_reason"):
        if bind.dialect.name == "postgresql":
            op.execute(
                "CREATE TYPE resolutionreason AS ENUM ('resolved', 'rebutted', 'superseded')"
            )
            op.add_column(
                "review_items",
                sa.Column(
                    "last_resolution_reason",
                    sa.Enum(
                        "resolved",
                        "rebutted",
                        "superseded",
                        name="resolutionreason",
                    ),
                    nullable=True,
                ),
            )
        else:
            op.add_column(
                "review_items",
                sa.Column(
                    "last_resolution_reason",
                    sa.String(length=32),
                    nullable=True,
                ),
            )

    if bind.dialect.name == "postgresql":
        # ``ADD VALUE`` cannot run inside a transaction. Wrap in
        # ``autocommit_block`` so the value is committed before we try to
        # UPDATE rows that reference it.
        ctx = op.get_context()
        with ctx.autocommit_block():
            op.execute(
                "ALTER TYPE reviewitemstatus ADD VALUE IF NOT EXISTS 'closed'"
            )

        # Backfill historical ``resolved`` rows into the new ``closed`` terminal.
        op.execute(
            "UPDATE review_items "
            "SET status = 'closed', last_resolution_reason = 'resolved' "
            "WHERE status = 'resolved'"
        )
    else:
        # SQLite test path: ``status`` is stored as VARCHAR; rewrite in-place.
        # ``autocommit_block`` ensures the UPDATE is committed even though
        # alembic runs migrations inside an outer transaction by default.
        ctx = op.get_context()
        with ctx.autocommit_block():
            bind.execute(
                sa.text(
                    "UPDATE review_items "
                    "SET status = 'closed', last_resolution_reason = 'resolved' "
                    "WHERE status = 'resolved'"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "review_items" not in inspector.get_table_names():
        return

    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE review_items "
            "SET status = 'resolved', last_resolution_reason = NULL "
            "WHERE status = 'closed' AND last_resolution_reason = 'resolved'"
        )
    else:
        op.execute(
            "UPDATE review_items "
            "SET status = 'resolved', last_resolution_reason = NULL "
            "WHERE status = 'closed' AND last_resolution_reason = 'resolved'"
        )

    if _has_column(inspector, "review_items", "last_resolution_reason"):
        op.drop_column("review_items", "last_resolution_reason")

    if bind.dialect.name == "postgresql":
        # Removing an enum value from PostgreSQL requires creating a new enum
        # without the value and rewriting the column. Skip in downgrade — the
        # value is harmless if unused, and operators can recreate it via
        # ``ALTER TYPE reviewitemstatus RENAME VALUE 'closed' TO ...`` if
        # a clean rollback is needed.
        pass
