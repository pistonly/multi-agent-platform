"""inbound event rejection_count for legacy v1 fingerprint path

Revision ID: 024
Revises: 023
Create Date: 2026-07-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "inbound_events" not in set(inspector.get_table_names()):
        return

    if not _has_column(inspector, "inbound_events", "rejection_count"):
        op.add_column(
            "inbound_events",
            sa.Column(
                "rejection_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "inbound_events" not in set(inspector.get_table_names()):
        return

    if _has_column(inspector, "inbound_events", "rejection_count"):
        op.drop_column("inbound_events", "rejection_count")
