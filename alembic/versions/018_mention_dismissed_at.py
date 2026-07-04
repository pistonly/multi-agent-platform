"""mentions.dismissed_at — let mentioned agents clear FYI/no-reply mentions from /todos

Revision ID: 018
Revises: 017
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mentions",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_mentions_mentioned_agent_id_dismissed_at",
        "mentions",
        ["mentioned_agent_id", "dismissed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mentions_mentioned_agent_id_dismissed_at",
        table_name="mentions",
    )
    op.drop_column("mentions", "dismissed_at")
