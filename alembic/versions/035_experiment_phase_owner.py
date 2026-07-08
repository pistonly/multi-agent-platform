"""experiments.phase_owner (experiment f873c287 I1(b))

Add ``phase_owner`` to ``experiments`` so the informational_only auto-
classification (I1(a)) and the "host blocked, waiting on {phase_owner}"
UI copy (I1(d)) have a per-row source of truth that doesn't need to be
re-computed on every read.

Decision-owner semantics (kept aligned with
``server.services.phase_owner_resolver``):

| phase          | owner     |
|----------------|-----------|
| draft          | host      |
| review         | reviewer  |
| approved       | host      |
| running        | host      |
| result_review  | reviewer  |
| done           | host      |
| cancelled      | host      |

Backfill strategy
-----------------
- Existing rows: set ``phase_owner`` to the resolver's owner for their
  current ``phase``. A single ``UPDATE experiments SET phase_owner = ...``
  per phase value works for both SQLite and PG.
- New rows: rely on the SQLAlchemy ``server_default='host'`` column
  default (set in ``server.domain.models``); application logic in
  ``phase_service`` overrides this to the right value on every phase
  transition.

SQLite vs PG
------------
The column is a plain ``VARCHAR(16)`` on both engines — we deliberately
do not use a DB-level enum so the application layer (resolver + enum)
stays the single source of truth. CHECK constraint is omitted because
SQLite ignores it on table rewrite anyway; the Pydantic enum catches
invalid values at the API boundary.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


# Kept in sync with ``server.services.phase_owner_resolver._OWNERS``.
# Mirrored here so the migration is self-contained even if the SDK /
# resolver aren't importable in the alembic env (e.g. running in a slim
# container). If the mapping drifts, the resolver's import-time
# ``assert set(_OWNERS) == ...`` will catch it.
_PHASE_OWNER_BACKFILL: dict[str, str] = {
    "draft": "host",
    "review": "reviewer",
    "approved": "host",
    "running": "host",
    "result_review": "reviewer",
    "done": "host",
    "cancelled": "host",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if _has_column(inspector, "experiments", "phase_owner"):
        return  # idempotent guard

    if bind.dialect.name == "postgresql":
        op.add_column(
            "experiments",
            sa.Column(
                "phase_owner",
                sa.String(length=16),
                nullable=False,
                server_default="host",
            ),
        )
        op.create_index(
            "ix_experiments_phase_owner",
            "experiments",
            ["phase_owner"],
        )
    else:
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "phase_owner",
                    sa.String(length=16),
                    nullable=False,
                    server_default="host",
                )
            )
        op.create_index(
            "ix_experiments_phase_owner",
            "experiments",
            ["phase_owner"],
        )

    # Backfill existing rows by phase value. server_default='host' will
    # have filled the column already; the per-phase UPDATEs only correct
    # the rows that ended up on a non-host phase with the wrong default.
    for phase_value, owner_value in _PHASE_OWNER_BACKFILL.items():
        op.execute(
            sa.text(
                "UPDATE experiments SET phase_owner = :owner WHERE phase = :phase"
            ).bindparams(owner=owner_value, phase=phase_value)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if not _has_column(inspector, "experiments", "phase_owner"):
        return
    op.drop_index("ix_experiments_phase_owner", table_name="experiments")
    with op.batch_alter_table("experiments") as batch_op:
        batch_op.drop_column("phase_owner")
