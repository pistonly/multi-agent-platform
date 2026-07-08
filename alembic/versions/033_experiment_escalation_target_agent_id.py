"""experiments.escalation_target_agent_id (experiment 156172e9 I1(b))

Adds a nullable ``escalation_target_agent_id`` column to ``experiments``
that lets a host creator override the role-based default escalation
contact for STATE_MACHINE.* errors. When the column is NULL, the CLI /
SDK falls back to the 3-tier role-based rule documented in the
experiment plan:

  1. Current caller agent (avoid cross-role mis-routing)
  2. Same-role + same-project active agent (any log/topic/review action
     in the last 7 days)
  3. Admin role (last-resort)

Backfill: existing experiments are left NULL — they take the role-based
path on first error. No renames, no enum changes, no data migration.

SQLite notes: ``ALTER TABLE ... ADD COLUMN`` cannot attach a FOREIGN
KEY constraint inline. We add the column as plain nullable, then use
``batch_alter_table`` (SQLite only) to attach the FK. PostgreSQL gets
the FK directly.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if _has_column(inspector, "experiments", "escalation_target_agent_id"):
        return  # idempotent guard — re-running upgrade must be a no-op

    if bind.dialect.name == "postgresql":
        op.add_column(
            "experiments",
            sa.Column(
                "escalation_target_agent_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_experiments_escalation_target_agent_id",
            "experiments",
            ["escalation_target_agent_id"],
        )
    else:
        # SQLite path: add column without FK, then use batch_alter_table
        # to attach the FK constraint (SQLite cannot ALTER ADD CONSTRAINT
        # outside of a table rebuild).
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "escalation_target_agent_id",
                    sa.String(length=36),
                    nullable=True,
                )
            )
            batch_op.create_foreign_key(
                "fk_experiments_escalation_target_agent_id",
                "agents",
                ["escalation_target_agent_id"],
                ["id"],
            )
            batch_op.create_index(
                "ix_experiments_escalation_target_agent_id",
                ["escalation_target_agent_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if not _has_column(inspector, "experiments", "escalation_target_agent_id"):
        return

    if bind.dialect.name == "postgresql":
        op.drop_index(
            "ix_experiments_escalation_target_agent_id",
            table_name="experiments",
        )
        op.drop_column("experiments", "escalation_target_agent_id")
    else:
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.drop_constraint(
                "fk_experiments_escalation_target_agent_id",
                type_="foreignkey",
            )
            batch_op.drop_index("ix_experiments_escalation_target_agent_id")
            batch_op.drop_column("escalation_target_agent_id")
