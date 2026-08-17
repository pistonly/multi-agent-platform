"""webhook_deliveries.last_error for retry diagnostics

为 ``webhook_deliveries`` 添加 ``last_error`` 列，用于记录最近一次投递失败的
错误信息（HTTP 状态码异常或异常堆栈摘要），便于 admin 在 deliveries 列表
看到失败原因、定位 webhook 配置问题。

配合 P1 #10 webhook 重试 + 日志改造：以前只能看到 ``status_code=None``，
无法区分是 DNS 失败、连接超时、还是 5xx。现在 ``last_error`` 会保留
``"{type(error)}: {error}"`` 或 ``"HTTP {status}"`` 形式的简短摘要。

Revision ID: 028
Revises: 027
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "webhook_deliveries" not in set(inspector.get_table_names()):
        return
    if not _has_column(inspector, "webhook_deliveries", "last_error"):
        op.add_column(
            "webhook_deliveries",
            sa.Column("last_error", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "webhook_deliveries" not in set(inspector.get_table_names()):
        return
    if _has_column(inspector, "webhook_deliveries", "last_error"):
        op.drop_column("webhook_deliveries", "last_error")
