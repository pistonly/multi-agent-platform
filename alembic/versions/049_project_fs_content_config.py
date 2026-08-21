"""P1: project-level FS content_root and freshness SLA.

Revision ID: 049
Revises: 048
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "049"
down_revision: str | None = "048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    columns = {c["name"] for c in inspector.get_columns("projects")}
    with op.batch_alter_table("projects") as batch:
        if "content_root" not in columns:
            batch.add_column(
                sa.Column(
                    "content_root",
                    sa.String(length=64),
                    nullable=False,
                    server_default="map",
                )
            )
        if "fs_freshness_sla_seconds" not in columns:
            batch.add_column(
                sa.Column("fs_freshness_sla_seconds", sa.Integer(), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    columns = {c["name"] for c in inspector.get_columns("projects")}
    with op.batch_alter_table("projects") as batch:
        if "fs_freshness_sla_seconds" in columns:
            batch.drop_column("fs_freshness_sla_seconds")
        if "content_root" in columns:
            batch.drop_column("content_root")
