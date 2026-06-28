"""topic and experiment archived_at

Revision ID: 013
Revises: 012
Create Date: 2026-06-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE = (
    "topic_id IS NOT NULL AND deleted_at IS NULL AND archived_at IS NULL "
    "AND phase IN ('draft','review','approved','running')"
)


def upgrade() -> None:
    op.add_column("topics", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("experiments", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_index("uq_experiment_one_active_per_topic", table_name="experiments")
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
    op.drop_column("experiments", "archived_at")
    op.drop_column("topics", "archived_at")
    _OLD_WHERE = (
        "topic_id IS NOT NULL AND deleted_at IS NULL "
        "AND phase IN ('draft','review','approved','running')"
    )
    op.create_index(
        "uq_experiment_one_active_per_topic",
        "experiments",
        ["topic_id"],
        unique=True,
        sqlite_where=sa.text(_OLD_WHERE),
        postgresql_where=sa.text(_OLD_WHERE),
    )
