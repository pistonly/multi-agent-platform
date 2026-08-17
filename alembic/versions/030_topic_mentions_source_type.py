"""add topic mention source type

Revision ID: 030
Revises: 029
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE mentionsourcetype ADD VALUE IF NOT EXISTS 'topic'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed cheaply and existing rows may use
    # this value. Keep downgrade data-preserving.
    pass
