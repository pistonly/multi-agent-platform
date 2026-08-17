"""topic_action_items escalation fields (category / cancel_reason / wake timeline)

补齐 ``topic_action_items`` 表的 6 个列：``category``、``cancel_reason``、
``wake_count``、``last_woken_at``、``first_open_at``、``stale_at``。

这些列原本由 ``server/db/session.py::_ENSURE_COLUMNS`` 在 ``init_db()`` 时
通过 ``ALTER TABLE`` 自愈，现在纳入 alembic 正式 migration，消除双轨维护。

Revision ID: 027
Revises: 026
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


# (column_name, column_def) — 与 server/domain/models.py TopicActionItem 对齐
_COLUMNS: list[tuple[str, sa.Column]] = [
    ("category", sa.Column("category", sa.String(length=32), nullable=True)),
    ("cancel_reason", sa.Column("cancel_reason", sa.Text(), nullable=True)),
    (
        "wake_count",
        sa.Column("wake_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    ),
    ("last_woken_at", sa.Column("last_woken_at", sa.DateTime(timezone=True), nullable=True)),
    ("first_open_at", sa.Column("first_open_at", sa.DateTime(timezone=True), nullable=True)),
    ("stale_at", sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "topic_action_items" not in set(inspector.get_table_names()):
        return

    for column_name, column in _COLUMNS:
        if not _has_column(inspector, "topic_action_items", column_name):
            op.add_column("topic_action_items", column)

    # Backfill first_open_at for existing open action items — 与原
    # _backfill_action_item_first_open_at 行为一致：把 created_at 当作首次打开时间，
    # 让 waker 可以正确计算 T+24h / T+72h 升级窗口。
    inspector = sa.inspect(bind)
    if (
        _has_column(inspector, "topic_action_items", "first_open_at")
        and _has_column(inspector, "topic_action_items", "created_at")
    ):
        op.execute(
            "UPDATE topic_action_items "
            "SET first_open_at = created_at "
            "WHERE first_open_at IS NULL AND status = 'open'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "topic_action_items" not in set(inspector.get_table_names()):
        return

    for column_name, _ in reversed(_COLUMNS):
        if _has_column(inspector, "topic_action_items", column_name):
            op.drop_column("topic_action_items", column_name)
