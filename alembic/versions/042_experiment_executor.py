"""experiment executor_agent_id

Add ``executor_agent_id`` column to ``experiments`` so the host (creator)
can delegate the *execution* of an experiment (``running`` phase advancement
via ``complete``) to another agent — typically a ``participant`` persona —
while keeping host-only lifecycle gates (create / submit-review / approve /
start / withdraw / cancel) on the creator.

Design:
- NULL is the backward-compat sentinel. Existing experiments keep
  ``executor_agent_id IS NULL``; ``phase_service.complete_experiment``
  falls back to ``creator_agent_id`` for the permission check so legacy
  host-self-executes behavior is preserved.
- ``start_experiment`` populates this column explicitly. When the host does
  not pass ``--executor``, the value defaults to ``actor.id`` (host
  self-executes) so the field is always set on new experiments.
- ``_ensure_result_reviewer`` is extended to block both creator AND
  executor from reviewing their own result.

SQLite notes: mirrors migration 033 — ``ALTER TABLE ... ADD COLUMN``
cannot attach a FOREIGN KEY inline, so the column is added as plain
nullable and ``batch_alter_table`` attaches the FK on SQLite only.
PostgreSQL gets the FK directly. Idempotent guard via ``_has_column``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "042"
down_revision: str | None = "041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if _has_column(inspector, "experiments", "executor_agent_id"):
        return  # idempotent guard — re-running upgrade must be a no-op

    if bind.dialect.name == "postgresql":
        op.add_column(
            "experiments",
            sa.Column(
                "executor_agent_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_experiments_executor_agent_id",
            "experiments",
            ["executor_agent_id"],
        )
    else:
        # SQLite path: add column without FK, then use batch_alter_table
        # to attach the FK constraint (SQLite cannot ALTER ADD CONSTRAINT
        # outside of a table rebuild).
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "executor_agent_id",
                    sa.String(length=36),
                    nullable=True,
                )
            )
            batch_op.create_foreign_key(
                "fk_experiments_executor_agent_id",
                "agents",
                ["executor_agent_id"],
                ["id"],
            )
            batch_op.create_index(
                "ix_experiments_executor_agent_id",
                ["executor_agent_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if not _has_column(inspector, "experiments", "executor_agent_id"):
        return

    if bind.dialect.name == "postgresql":
        op.drop_index(
            "ix_experiments_executor_agent_id",
            table_name="experiments",
        )
        op.drop_column("experiments", "executor_agent_id")
    else:
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.drop_constraint(
                "fk_experiments_executor_agent_id",
                type_="foreignkey",
            )
            batch_op.drop_index("ix_experiments_executor_agent_id")
            batch_op.drop_column("executor_agent_id")
