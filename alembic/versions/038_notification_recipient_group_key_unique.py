"""notifications UNIQUE(recipient_agent_id, group_key) constraint

Goal
----
The ORM model ``Notification`` already declares
``UniqueConstraint("recipient_agent_id", "group_key", name="uq_notifications_recipient_group_key")``
(see ``server/domain/models.py``), and ``notification_service._upsert_notification``
already uses ``ON CONFLICT (recipient_agent_id, group_key) DO UPDATE``. However,
no migration ever created this constraint in the database — migration 023 only
added a plain index ``ix_notifications_group_key``, and migration 036 explicitly
listed this constraint as "Out of scope (PR2)".

Without the constraint, every notification upsert raises::

    sqlite3.OperationalError: ON CONFLICT clause does not match any
    PRIMARY KEY or UNIQUE constraint

This manifests as HTTP 500 on ``POST /topics/{id}/comments`` (and any other
write that fans out a notification), even though the primary write (e.g. the
comment) succeeds.

This migration closes the gap by creating the UNIQUE constraint.

Dedup
-----
Before creating the constraint we delete duplicate rows per
``(recipient_agent_id, group_key)`` (where ``group_key IS NOT NULL``), keeping
the one with the latest ``updated_at``. In practice the upsert bug means most
deployments have zero notification rows with non-NULL ``group_key``, but the
dedup is a safety net for databases that accumulated duplicates through other
code paths.

See also: platform feedback 351cd4c2-0223-4451-853a-1951c146f96e.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT_NAME = "uq_notifications_recipient_group_key"


def _has_unique_constraint(inspector: sa.Inspector, table_name: str, name: str) -> bool:
    return any(
        const["name"] == name
        for const in inspector.get_unique_constraints(table_name)
    )


def _has_index(inspector: sa.Inspector, table_name: str, name: str) -> bool:
    return any(idx["name"] == name for idx in inspector.get_indexes(table_name))


def _dedup_notifications(bind) -> None:
    """Delete duplicate notification rows per (recipient_agent_id, group_key).

    Keeps the row with the latest ``updated_at`` (ties broken by ``id`` to
    remain deterministic). Only affects rows where ``group_key IS NOT NULL``.
    """
    bind.execute(sa.text("""
        DELETE FROM notifications
        WHERE group_key IS NOT NULL
          AND id NOT IN (
            SELECT id FROM (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY recipient_agent_id, group_key
                       ORDER BY updated_at DESC, id DESC
                     ) AS rn
              FROM notifications
              WHERE group_key IS NOT NULL
            )
            WHERE rn = 1
          )
    """))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "notifications" not in inspector.get_table_names():
        return

    # Idempotent guard — constraint already exists.
    if _has_unique_constraint(inspector, "notifications", _CONSTRAINT_NAME):
        return
    if _has_index(inspector, "notifications", _CONSTRAINT_NAME):
        return  # PG partial unique index already created

    # Safety net: remove duplicates before adding the constraint.
    _dedup_notifications(bind)

    if bind.dialect.name == "postgresql":
        # Partial unique index — NULL group_keys are excluded so multiple
        # digest-less notifications per recipient are allowed (matches the
        # ORM model's documented intent).
        op.create_index(
            _CONSTRAINT_NAME,
            "notifications",
            ["recipient_agent_id", "group_key"],
            unique=True,
            postgresql_where=sa.text("group_key IS NOT NULL"),
        )
    else:
        # SQLite: UNIQUE constraint allows multiple NULLs by default, so no
        # partial-index equivalent is needed. batch_alter_table recreates
        # the table behind the scenes to add the constraint.
        with op.batch_alter_table("notifications") as batch_op:
            batch_op.create_unique_constraint(
                _CONSTRAINT_NAME,
                ["recipient_agent_id", "group_key"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "notifications" not in inspector.get_table_names():
        return

    if bind.dialect.name == "postgresql":
        if _has_index(inspector, "notifications", _CONSTRAINT_NAME):
            op.drop_index(_CONSTRAINT_NAME, table_name="notifications")
    else:
        if _has_unique_constraint(inspector, "notifications", _CONSTRAINT_NAME):
            with op.batch_alter_table("notifications") as batch_op:
                batch_op.drop_constraint(_CONSTRAINT_NAME, type_="unique")
