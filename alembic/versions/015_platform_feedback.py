"""platform feedback inbox

Revision ID: 015
Revises: 014
Create Date: 2026-06-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("author_agent_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Enum("bug", "suggestion", "question", "other", name="feedbackcategory"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum("new", "triaged", "in_progress", "resolved", name="feedbackstatus"),
            nullable=False,
            server_default="new",
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_platform_feedback_author_agent_id"),
        "platform_feedback",
        ["author_agent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_platform_feedback_project_id"),
        "platform_feedback",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_platform_feedback_status"),
        "platform_feedback",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_platform_feedback_status"), table_name="platform_feedback")
    op.drop_index(op.f("ix_platform_feedback_project_id"), table_name="platform_feedback")
    op.drop_index(op.f("ix_platform_feedback_author_agent_id"), table_name="platform_feedback")
    op.drop_table("platform_feedback")
