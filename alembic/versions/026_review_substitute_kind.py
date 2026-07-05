"""review substitute_kind (experiment plan v2 I3)

Revision ID: 026
Revises: 025
Create Date: 2026-07-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SUBSTITUTE_KIND = sa.Enum(
    "none",
    "admin_for_others",
    "admin_self_substitute",
    name="reviewsubstitutekind",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "reviews" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("reviews")}
    if "substitute_kind" in columns:
        return
    _SUBSTITUTE_KIND.create(bind, checkfirst=True)
    op.add_column(
        "reviews",
        sa.Column(
            "substitute_kind",
            _SUBSTITUTE_KIND,
            nullable=False,
            server_default="none",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "reviews" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("reviews")}
    if "substitute_kind" not in columns:
        return
    op.drop_column("reviews", "substitute_kind")
    _SUBSTITUTE_KIND.drop(bind, checkfirst=True)
