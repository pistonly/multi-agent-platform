"""topic comment is_round_summary

Add a boolean column to topic_comments so Round Summary comments can be
explicitly marked at creation time instead of relying solely on regex
detection of the comment body. Existing comments matching the regex are
backfilled to is_round_summary=True.

Revision ID: 040
Revises: 039
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("topic_comments") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_round_summary",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
    # Backfill: mark existing host top-level comments matching the regex.
    op.execute(
        """
        UPDATE topic_comments
        SET is_round_summary = 1
        WHERE parent_comment_id IS NULL
          AND body LIKE '%## Round%Summary%'
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("topic_comments") as batch_op:
        batch_op.drop_column("is_round_summary")
