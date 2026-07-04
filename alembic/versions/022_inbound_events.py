"""runtime-waker inbound event log (D1: access-layer audit + dedup gate)

Revision ID: 022
Revises: 021
Create Date: 2026-07-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INBOUND_EVENT_SOURCE_ENUM_NAME = "inboundeventsource"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "inbound_events" not in existing:
        op.create_table(
            "inbound_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("agent_id", sa.Uuid(), nullable=False),
            sa.Column("event_id", sa.Uuid(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column(
                "source",
                sa.Enum(
                    "polling",
                    "sse",
                    "replay",
                    name=_INBOUND_EVENT_SOURCE_ENUM_NAME,
                ),
                nullable=False,
                server_default="polling",
            ),
            sa.Column("fingerprint", sa.String(length=128), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column(
                "received_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("fingerprint", name="uq_inbound_events_fingerprint"),
        )
        op.create_index(
            "ix_inbound_events_agent_source_received",
            "inbound_events",
            ["agent_id", "source", "received_at"],
        )
        op.create_index(
            "ix_inbound_events_event_id", "inbound_events", ["event_id"]
        )
        op.create_index(
            "ix_inbound_events_received_at", "inbound_events", ["received_at"]
        )


def downgrade() -> None:
    op.drop_index(
        "ix_inbound_events_received_at", table_name="inbound_events"
    )
    op.drop_index("ix_inbound_events_event_id", table_name="inbound_events")
    op.drop_index(
        "ix_inbound_events_agent_source_received", table_name="inbound_events"
    )
    op.drop_table("inbound_events")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            f"DROP TYPE IF EXISTS {_INBOUND_EVENT_SOURCE_ENUM_NAME}"
        )
