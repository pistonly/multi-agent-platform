"""flexible discussion rounds

Change topics.discussion_round from a fixed Enum("round1","round2","ready")
to VARCHAR(20) so the platform supports arbitrary roundN values (round3,
round4, ...). The "ready" terminal state is preserved; reaching it is now a
host decision via advance-round --ready rather than an automatic transition
after round2.

Revision ID: 039
Revises: 038
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite batch mode: batch_alter_table handles Enum → String conversion.
    # PostgreSQL: ALTER COLUMN TYPE VARCHAR(20) is implicit-safe for existing
    # enum values ("round1", "round2", "ready") since they are all short strings.
    with op.batch_alter_table("topics") as batch_op:
        batch_op.alter_column(
            "discussion_round",
            existing_type=sa.Enum("round1", "round2", "ready", name="topicdiscussionround"),
            type_=sa.String(length=20),
            existing_nullable=False,
            existing_server_default="round1",
        )


def downgrade() -> None:
    # Note: any round3+ values will be lost on downgrade (mapped to NULL/round1).
    with op.batch_alter_table("topics") as batch_op:
        batch_op.alter_column(
            "discussion_round",
            existing_type=sa.String(length=20),
            type_=sa.Enum("round1", "round2", "ready", name="topicdiscussionround"),
            existing_nullable=False,
            existing_server_default="round1",
        )
