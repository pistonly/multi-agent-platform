"""topic pinned flag

Revision ID: 011
Revises: 010
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.add_column(
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.drop_column("pinned")
