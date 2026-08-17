"""topics and topic comments

Revision ID: 006
Revises: 005
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("creator_agent_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("open", "closed", name="topicstatus"), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["creator_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_topics_project_id"), "topics", ["project_id"], unique=False)
    op.create_index(op.f("ix_topics_status"), "topics", ["status"], unique=False)

    op.create_table(
        "topic_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("author_agent_id", sa.Uuid(), nullable=False),
        sa.Column("parent_comment_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["author_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["parent_comment_id"], ["topic_comments.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_topic_comments_topic_id"), "topic_comments", ["topic_id"], unique=False)

    with op.batch_alter_table("experiments") as batch_op:
        batch_op.add_column(sa.Column("topic_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_experiments_topic_id", ["topic_id"], unique=False)
        batch_op.create_foreign_key("fk_experiments_topic_id", "topics", ["topic_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("experiments") as batch_op:
        batch_op.drop_constraint("fk_experiments_topic_id", type_="foreignkey")
        batch_op.drop_index("ix_experiments_topic_id")
        batch_op.drop_column("topic_id")

    op.drop_index(op.f("ix_topic_comments_topic_id"), table_name="topic_comments")
    op.drop_table("topic_comments")

    op.drop_index(op.f("ix_topics_status"), table_name="topics")
    op.drop_index(op.f("ix_topics_project_id"), table_name="topics")
    op.drop_table("topics")
    op.execute("DROP TYPE IF EXISTS topicstatus")
