"""reviews comments plans phases

Revision ID: 002
Revises: 001
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_agent_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["reviewer_agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "reviewer_agent_id", "plan_version", name="uq_review_per_version"),
    )
    op.create_index(op.f("ix_reviews_experiment_id"), "reviews", ["experiment_id"], unique=False)

    op.create_table(
        "review_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Enum("reasonable", "unreasonable", name="reviewitemkind"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "addressed",
                "rebutted",
                "resolved",
                "withdrawn",
                "escalated",
                name="reviewitemstatus",
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_items_review_id"), "review_items", ["review_id"], unique=False)

    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_type", sa.Enum("plan", "review", "review_item", "comment", name="commentanchortype"), nullable=False),
        sa.Column("anchor_id", sa.Uuid(), nullable=False),
        sa.Column("parent_comment_id", sa.Uuid(), nullable=True),
        sa.Column("author_agent_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["author_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["parent_comment_id"], ["comments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_comments_experiment_id"), "comments", ["experiment_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_comments_experiment_id"), table_name="comments")
    op.drop_table("comments")
    op.drop_index(op.f("ix_review_items_review_id"), table_name="review_items")
    op.drop_table("review_items")
    op.drop_index(op.f("ix_reviews_experiment_id"), table_name="reviews")
    op.drop_table("reviews")
