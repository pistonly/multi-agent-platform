"""comment @mention records

Revision ID: 010
Revises: 009
Create Date: 2026-06-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mentioned_agent_id", sa.Uuid(), nullable=False),
        sa.Column("author_agent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum("experiment_comment", "topic_comment", name="mentionsourcetype"),
            nullable=False,
        ),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("excerpt", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["mentioned_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["author_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mentions_mentioned_agent_id", "mentions", ["mentioned_agent_id"])
    op.create_index("ix_mentions_created_at", "mentions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mentions_created_at", table_name="mentions")
    op.drop_index("ix_mentions_mentioned_agent_id", table_name="mentions")
    op.drop_table("mentions")
