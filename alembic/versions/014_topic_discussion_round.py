"""topic discussion round

Revision ID: 014
Revises: 013
Create Date: 2026-06-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.add_column(
            sa.Column(
                "discussion_round",
                sa.Enum("round1", "round2", "ready", name="topicdiscussionround"),
                nullable=False,
                server_default="round1",
            )
        )
        batch_op.add_column(
            sa.Column("round_summary_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_index(
            batch_op.f("ix_topics_discussion_round"),
            ["discussion_round"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.drop_index(batch_op.f("ix_topics_discussion_round"))
        batch_op.drop_column("round_summary_count")
        batch_op.drop_column("discussion_round")
