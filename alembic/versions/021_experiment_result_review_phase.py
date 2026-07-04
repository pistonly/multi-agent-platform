"""experiment result review phase

Revision ID: 021
Revises: 020
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE = (
    "topic_id IS NOT NULL AND deleted_at IS NULL AND archived_at IS NULL "
    "AND phase IN ('draft','review','approved','running','result_review')"
)
_OLD_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE = (
    "topic_id IS NOT NULL AND deleted_at IS NULL AND archived_at IS NULL "
    "AND phase IN ('draft','review','approved','running')"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        ctx = op.get_context()
        with ctx.autocommit_block():
            op.execute("ALTER TYPE experimentphase ADD VALUE IF NOT EXISTS 'result_review'")
    op.drop_index("uq_experiment_one_active_per_topic", table_name="experiments")
    op.create_index(
        "uq_experiment_one_active_per_topic",
        "experiments",
        ["topic_id"],
        unique=True,
        sqlite_where=sa.text(_NEW_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
        postgresql_where=sa.text(_NEW_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_experiment_one_active_per_topic", table_name="experiments")
    op.create_index(
        "uq_experiment_one_active_per_topic",
        "experiments",
        ["topic_id"],
        unique=True,
        sqlite_where=sa.text(_OLD_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
        postgresql_where=sa.text(_OLD_ACTIVE_TOPIC_EXPERIMENT_INDEX_WHERE),
    )
