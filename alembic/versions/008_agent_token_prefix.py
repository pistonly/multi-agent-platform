"""agent token prefix for indexed auth lookup

Revision ID: 008
Revises: 007
Create Date: 2026-06-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column("api_token_prefix", sa.String(length=8), nullable=False, server_default="")
        )
        batch_op.create_index("ix_agents_api_token_prefix", ["api_token_prefix"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_index("ix_agents_api_token_prefix")
        batch_op.drop_column("api_token_prefix")
