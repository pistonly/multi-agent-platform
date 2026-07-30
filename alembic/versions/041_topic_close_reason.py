"""topic close_reason and close_note

Add ``close_reason`` and ``close_note`` columns to the ``topics`` table so the
host can record *why* a topic was closed — e.g. "discussed but decided not to
create an experiment" — rather than treating all closures identically.

Revision ID: 041
Revises: 040
Create Date: 2026-07-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.add_column(
            sa.Column("close_reason", sa.String(256), nullable=True)
        )
        batch_op.add_column(
            sa.Column("close_note", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.drop_column("close_note")
        batch_op.drop_column("close_reason")
