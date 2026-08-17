"""topic decisions and action items

Revision ID: 016
Revises: 015
Create Date: 2026-06-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "topic_decisions" not in existing:
        op.create_table(
            "topic_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("author_agent_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("rejected_options", sa.Text(), nullable=True),
        sa.Column("open_questions", sa.Text(), nullable=True),
        sa.Column("no_decision_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["author_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", name="uq_topic_decision_topic_id"),
        )
        op.create_index(op.f("ix_topic_decisions_project_id"), "topic_decisions", ["project_id"], unique=False)
        op.create_index(op.f("ix_topic_decisions_topic_id"), "topic_decisions", ["topic_id"], unique=False)

    if "topic_action_items" not in existing:
        op.create_table(
            "topic_action_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_agent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "done", "cancelled", name="topicactionitemstatus"),
            nullable=False,
            server_default="open",
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_experiment_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["decision_id"], ["topic_decisions.id"]),
        sa.ForeignKeyConstraint(["linked_experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["owner_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
        sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_topic_action_items_decision_id"), "topic_action_items", ["decision_id"], unique=False)
        op.create_index(
            op.f("ix_topic_action_items_linked_experiment_id"),
            "topic_action_items",
            ["linked_experiment_id"],
            unique=False,
        )
        op.create_index(op.f("ix_topic_action_items_owner_agent_id"), "topic_action_items", ["owner_agent_id"], unique=False)
        op.create_index(op.f("ix_topic_action_items_project_id"), "topic_action_items", ["project_id"], unique=False)
        op.create_index(op.f("ix_topic_action_items_status"), "topic_action_items", ["status"], unique=False)
        op.create_index(op.f("ix_topic_action_items_topic_id"), "topic_action_items", ["topic_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_topic_action_items_topic_id"), table_name="topic_action_items")
    op.drop_index(op.f("ix_topic_action_items_status"), table_name="topic_action_items")
    op.drop_index(op.f("ix_topic_action_items_project_id"), table_name="topic_action_items")
    op.drop_index(op.f("ix_topic_action_items_owner_agent_id"), table_name="topic_action_items")
    op.drop_index(op.f("ix_topic_action_items_linked_experiment_id"), table_name="topic_action_items")
    op.drop_index(op.f("ix_topic_action_items_decision_id"), table_name="topic_action_items")
    op.drop_table("topic_action_items")
    op.drop_index(op.f("ix_topic_decisions_topic_id"), table_name="topic_decisions")
    op.drop_index(op.f("ix_topic_decisions_project_id"), table_name="topic_decisions")
    op.drop_table("topic_decisions")
    op.execute("DROP TYPE IF EXISTS topicactionitemstatus")
