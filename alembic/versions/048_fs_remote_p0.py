"""Remote FS P0: single publisher CAS and one-time write receipts.

Revision ID: 048
Revises: 047
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "048"
down_revision: str | None = "047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    columns = {c["name"] for c in inspector.get_columns("fs_projections")}

    with op.batch_alter_table("fs_projections") as batch:
        if "publisher_agent_id" not in columns:
            batch.add_column(sa.Column("publisher_agent_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_fs_projections_publisher_agent_id_agents",
                "agents",
                ["publisher_agent_id"],
                ["id"],
            )
        if "owner_agent_id" not in columns:
            batch.add_column(sa.Column("owner_agent_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_fs_projections_owner_agent_id_agents",
                "agents",
                ["owner_agent_id"],
                ["id"],
            )
        if "revision" not in columns:
            batch.add_column(
                sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
            )
        if "content_hash" not in columns:
            batch.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))

    if "fs_write_receipts" not in inspector.get_table_names():
        op.create_table(
            "fs_write_receipts",
            sa.Column("nonce", sa.String(length=64), primary_key=True),
            sa.Column(
                "project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False
            ),
            sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("slug", sa.String(length=255), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("base_revision", sa.Integer(), nullable=False),
            sa.Column(
                "committed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_fs_write_receipts_project_id",
            "fs_write_receipts",
            ["project_id"],
        )
        op.create_index(
            "ix_fs_write_receipts_agent_id", "fs_write_receipts", ["agent_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if "fs_write_receipts" in inspector.get_table_names():
        op.drop_index("ix_fs_write_receipts_agent_id", table_name="fs_write_receipts")
        op.drop_index("ix_fs_write_receipts_project_id", table_name="fs_write_receipts")
        op.drop_table("fs_write_receipts")

    columns = {c["name"] for c in inspector.get_columns("fs_projections")}
    with op.batch_alter_table("fs_projections") as batch:
        if "owner_agent_id" in columns:
            batch.drop_constraint(
                "fk_fs_projections_owner_agent_id_agents", type_="foreignkey"
            )
        if "publisher_agent_id" in columns:
            batch.drop_constraint(
                "fk_fs_projections_publisher_agent_id_agents", type_="foreignkey"
            )
        for name in ("content_hash", "revision", "owner_agent_id", "publisher_agent_id"):
            if name in columns:
                batch.drop_column(name)
