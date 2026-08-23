"""Agent waker-heartbeat timestamps.

D1 (topic waker-heartbeat-visibility): add last_api_seen_at / last_waker_poll_at
to ``agents``. Both are nullable timestamps; stale detection reads only
last_waker_poll_at so manual (non-waker) work does not pollute liveness.

Revision ID: 050
Revises: 049
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "050"
down_revision: str | None = "049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    columns = {c["name"] for c in inspector.get_columns("agents")}
    with op.batch_alter_table("agents") as batch:
        if "last_api_seen_at" not in columns:
            batch.add_column(
                sa.Column("last_api_seen_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "last_waker_poll_at" not in columns:
            batch.add_column(
                sa.Column("last_waker_poll_at", sa.DateTime(timezone=True), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    columns = {c["name"] for c in inspector.get_columns("agents")}
    with op.batch_alter_table("agents") as batch:
        if "last_waker_poll_at" in columns:
            batch.drop_column("last_waker_poll_at")
        if "last_api_seen_at" in columns:
            batch.drop_column("last_api_seen_at")
