"""Project-level feature flags (实验 M2 I4：A4).

Project-scoped flag table. The single registered key today is
``fs_stop_duplicate_insert`` (分阶段切换 + kill switch + fail closed
mechanism — see M2 plan §A4). Schema is intentionally generic so future
flags can land without another migration; only ``fs_stop_duplicate_insert``
is consumed by the gate right now.

- ``flag_value`` is a short string column (not JSON) — phase/key values
  like ``off`` / ``phase1`` / ``on`` stay readable in pg dump / sqlite
  shell without extra tooling.
- ``reason`` is the rationale the host/admin wrote at flip time; surfaces
  in ``map project config flag list`` for audit. No length cap on reason
  (free text), but typical entries are one paragraph.
- ``set_by_agent_id`` is NOT NULL — every flip is attributed (no anonymous
  defaults; even bootstrap flips carry a host/admin actor).

Downgrade drops the table; flag state is intentionally not preserved.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "053"
down_revision: str | None = "052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if "project_feature_flags" in inspector.get_table_names():
        return  # idempotent guard — re-running upgrade must be a no-op

    op.create_table(
        "project_feature_flags",
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("flag_key", sa.String(length=64), nullable=False),
        sa.Column("flag_value", sa.String(length=32), nullable=False),
        sa.Column("set_by_agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column(
            "set_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "flag_key", name="pk_project_feature_flags"),
    )
    op.create_index(
        "ix_project_feature_flags_project_id",
        "project_feature_flags",
        ["project_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if "project_feature_flags" not in inspector.get_table_names():
        return  # idempotent guard

    op.drop_index("ix_project_feature_flags_project_id", table_name="project_feature_flags")
    op.drop_table("project_feature_flags")
