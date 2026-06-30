"""topics.dismissed_at — let host hide a parked/open topic from /todos until next activity

Revision ID: 019
Revises: 018
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the FK column first to avoid SQLite batch-mode circular dependency
    # detection when both new columns are added together.
    with op.batch_alter_table("topics") as batch:
        batch.add_column(
            sa.Column(
                "dismissed_by_agent_id",
                sa.Uuid(),
                sa.ForeignKey("agents.id", name="fk_topics_dismissed_by_agent_id"),
                nullable=True,
            )
        )
    with op.batch_alter_table("topics") as batch:
        batch.add_column(
            sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.create_index(
        "ix_topics_creator_agent_id_dismissed_at",
        "topics",
        ["creator_agent_id", "dismissed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_topics_creator_agent_id_dismissed_at", table_name="topics")
    with op.batch_alter_table("topics") as batch:
        batch.drop_column("dismissed_at")
    with op.batch_alter_table("topics") as batch:
        batch.drop_column("dismissed_by_agent_id")
