"""add log_index to experiment_logs

Revision ID: 004
Revises: 003
Create Date: 2026-06-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_logs") as batch_op:
        batch_op.add_column(sa.Column("log_index", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE experiment_logs
        SET log_index = (
            SELECT COUNT(*) FROM experiment_logs AS e2
            WHERE e2.experiment_id = experiment_logs.experiment_id
              AND e2.created_at <= experiment_logs.created_at
        )
        """
    )
    with op.batch_alter_table("experiment_logs") as batch_op:
        batch_op.alter_column("log_index", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("experiment_logs") as batch_op:
        batch_op.drop_column("log_index")
