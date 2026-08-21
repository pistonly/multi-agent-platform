"""Deployment matrix: ``fs_projections`` cache table.

Remote/container deployments cannot see ``project.workspace_path``, so the FS
plane (``map/`` folder source of truth) silently vanished from server reads.
This migration adds the read-side fallback store used by ``map fs push``:

- one row per project (unique ``project_id``), idempotent overwrite on push;
- ``payload_json`` holds the pushed ``FsProjectionPushRequest`` snapshot
  (topic details with comment metadata/content);
- read paths fall back to this snapshot only when the workspace content root
  is unreachable (see ``fs_source_service.fs_plane_status``);
- validated writes (advance-round / close) commit back by applying the signed
  ``fields`` onto the cached snapshot, so no extra push round-trip is needed.

Downgrade drops the table — local file sovereignty is untouched either way.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "047"
down_revision: str | None = "046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if "fs_projections" in inspector.get_table_names():
        return  # idempotent guard — re-running upgrade must be a no-op

    op.create_table(
        "fs_projections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "pushed_by_agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=True,
        ),
        sa.Column("client_workspace", sa.String(length=1024), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "pushed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_fs_projections_project_id", "fs_projections", ["project_id"], unique=True
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if "fs_projections" not in inspector.get_table_names():
        return  # idempotent guard

    op.drop_index("ix_fs_projections_project_id", table_name="fs_projections")
    op.drop_table("fs_projections")
