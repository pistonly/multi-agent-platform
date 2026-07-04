"""notification category and aggregation fields

Revision ID: 023
Revises: 022
Create Date: 2026-07-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIFICATION_CATEGORY_ENUM_NAME = "notificationcategory"
_NOTIFICATION_FINGERPRINT_VERSION_ENUM_NAME = "notificationfingerprintversion"


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "notifications" not in set(inspector.get_table_names()):
        return

    category_type = sa.Enum(
        "wakeable",
        "digest",
        name=_NOTIFICATION_CATEGORY_ENUM_NAME,
    )
    if bind.dialect.name == "postgresql":
        category_type.create(bind, checkfirst=True)

    if not _has_column(inspector, "notifications", "category"):
        op.add_column(
            "notifications",
            sa.Column(
                "category",
                category_type,
                nullable=False,
                server_default="digest",
            ),
        )
        op.create_index("ix_notifications_category", "notifications", ["category"])
    if not _has_column(inspector, "notifications", "group_key"):
        op.add_column("notifications", sa.Column("group_key", sa.String(length=512), nullable=True))
        op.create_index("ix_notifications_group_key", "notifications", ["group_key"])
    if not _has_column(inspector, "notifications", "wake_version"):
        op.add_column(
            "notifications",
            sa.Column("wake_version", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _has_column(inspector, "notifications", "event_count"):
        op.add_column(
            "notifications",
            sa.Column("event_count", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _has_column(inspector, "notifications", "first_event_at"):
        op.add_column(
            "notifications",
            sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "notifications", "last_event_at"):
        op.add_column(
            "notifications",
            sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "notifications", "updated_at"):
        op.add_column(
            "notifications",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )

    if not _has_column(inspector, "notifications", "fingerprint_version"):
        fingerprint_version_type = sa.Enum(
            "v1",
            "v2",
            name=_NOTIFICATION_FINGERPRINT_VERSION_ENUM_NAME,
        )
        if bind.dialect.name == "postgresql":
            fingerprint_version_type.create(bind, checkfirst=True)
        op.add_column(
            "notifications",
            sa.Column(
                "fingerprint_version",
                fingerprint_version_type,
                nullable=False,
                server_default="v2",
            ),
        )
        op.create_index(
            "ix_notifications_fingerprint_version",
            "notifications",
            ["fingerprint_version"],
        )

    op.execute("UPDATE notifications SET first_event_at = COALESCE(first_event_at, created_at)")
    op.execute("UPDATE notifications SET last_event_at = COALESCE(last_event_at, created_at)")
    op.execute("UPDATE notifications SET updated_at = COALESCE(updated_at, created_at)")
    op.execute(
        "UPDATE notifications SET fingerprint_version = 'v2' WHERE fingerprint_version IS NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "notifications" not in set(inspector.get_table_names()):
        return

    for index_name in (
        "ix_notifications_fingerprint_version",
        "ix_notifications_group_key",
        "ix_notifications_category",
    ):
        try:
            op.drop_index(index_name, table_name="notifications")
        except Exception:
            pass
    for column_name in (
        "fingerprint_version",
        "updated_at",
        "last_event_at",
        "first_event_at",
        "event_count",
        "wake_version",
        "group_key",
        "category",
    ):
        if _has_column(inspector, "notifications", column_name):
            op.drop_column("notifications", column_name)
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP TYPE IF EXISTS {_NOTIFICATION_FINGERPRINT_VERSION_ENUM_NAME}")
        op.execute(f"DROP TYPE IF EXISTS {_NOTIFICATION_CATEGORY_ENUM_NAME}")
