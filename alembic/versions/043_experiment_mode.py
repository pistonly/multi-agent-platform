"""experiment mode (v0.10: direct / standard)

Add ``mode`` column to ``experiments`` so the host can create experiments
in ``direct`` mode — a fast path that skips reviewer gates
(``draft → running → done``) and delegates execution to a participant.

Design:
- ``standard`` is the default and backward-compat value. Existing
  experiments keep ``mode='standard'``; no backfill needed.
- ``direct`` mode is immutable after creation: the state machine, phase
  owner resolver, and todos projection all branch on this field.
- The column is a plain ``VARCHAR(16)`` (not an enum) to match the
  pattern used by ``phase_owner`` (migration 035) — the Python
  ``ExperimentMode`` enum remains the single source of truth.

Idempotent: re-running upgrade is a no-op if the column already exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "043"
down_revision: str | None = "042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if _has_column(inspector, "experiments", "mode"):
        return  # idempotent guard

    op.add_column(
        "experiments",
        sa.Column(
            "mode",
            sa.String(length=16),
            nullable=False,
            server_default="standard",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if not _has_column(inspector, "experiments", "mode"):
        return

    op.drop_column("experiments", "mode")
