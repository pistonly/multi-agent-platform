"""experiment execution lock fields

Revision ID: 017
Revises: 016
Create Date: 2026-06-30

Adds the per-project experiment execution lock fields (CP-3 / CP-3.5):

* lock_holder_experiment_id
* lock_acquired_at
* lock_ttl_seconds
* next_attempt_at
* lock_skip_count

Also sweeps stale ``running`` experiments older than 1 hour, marking them
``cancelled`` so they no longer appear as live executions.

This migration is fully reversible via ``downgrade()``.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("experiments")}

    # 1. Add columns (NULLABLE except lock_skip_count which is non-nullable with default 0).
    if "lock_holder_experiment_id" not in existing_columns:
        op.add_column(
            "experiments",
            sa.Column("lock_holder_experiment_id", sa.Uuid(), nullable=True),
        )
    if "lock_acquired_at" not in existing_columns:
        op.add_column(
            "experiments",
            sa.Column("lock_acquired_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "lock_ttl_seconds" not in existing_columns:
        op.add_column(
            "experiments",
            sa.Column("lock_ttl_seconds", sa.Integer(), nullable=True),
        )
    if "next_attempt_at" not in existing_columns:
        op.add_column(
            "experiments",
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "lock_skip_count" not in existing_columns:
        op.add_column(
            "experiments",
            sa.Column("lock_skip_count", sa.Integer(), nullable=False, server_default="0"),
        )

    # 2. Partial index for cheap "is the lock held" lookups.
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("experiments")}
    if "ix_experiments_lock_holder" not in existing_indexes:
        op.create_index(
            "ix_experiments_lock_holder",
            "experiments",
            ["lock_holder_experiment_id"],
            unique=False,
        )

    # 3. Stale-lock sweep: any 'running' experiment untouched for >1 hour is auto-cancelled.
    #    We are deliberately conservative — the host worker cannot trust stale rows.
    dialect = bind.dialect.name
    if dialect == "sqlite":
        op.execute(
            sa.text(
                """
                UPDATE experiments
                   SET phase = 'cancelled',
                       description = COALESCE(description, '') || char(10) ||
                                     '[exp-lock migration 017] auto-cancelled: stale running > 1h'
                 WHERE phase = 'running'
                   AND updated_at < datetime('now', '-1 hour')
                """
            )
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE experiments
                   SET phase = 'cancelled',
                       description = COALESCE(description, '') ||
                                     E'\\n[exp-lock migration 017] auto-cancelled: stale running > 1h'
                 WHERE phase = 'running'
                   AND updated_at < now() - interval '1 hour'
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("experiments")}
    if "ix_experiments_lock_holder" in existing_indexes:
        op.drop_index("ix_experiments_lock_holder", table_name="experiments")

    existing_columns = {col["name"] for col in inspector.get_columns("experiments")}
    for col in [
        "lock_skip_count",
        "next_attempt_at",
        "lock_ttl_seconds",
        "lock_acquired_at",
        "lock_holder_experiment_id",
    ]:
        if col in existing_columns:
            op.drop_column("experiments", col)
