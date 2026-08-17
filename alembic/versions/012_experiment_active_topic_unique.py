"""one active experiment per topic

Revision ID: 012
Revises: 011
Create Date: 2026-06-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE = (
    "topic_id IS NOT NULL AND deleted_at IS NULL "
    "AND phase IN ('draft','review','approved','running')"
)


def upgrade() -> None:
    op.create_index(
        "uq_experiment_one_active_per_topic",
        "experiments",
        ["topic_id"],
        unique=True,
        sqlite_where=sa.text(_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
        postgresql_where=sa.text(_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_experiment_one_active_per_topic", table_name="experiments")
