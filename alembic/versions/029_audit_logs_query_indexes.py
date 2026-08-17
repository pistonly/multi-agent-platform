"""audit_logs query indexes (target_type+target_id, created_at)

为 ``audit_logs`` 添加两个查询索引，对应 ``audit_service`` 的两个查询入口：

1. ``ix_audit_logs_target(target_type, target_id)`` — 服务
   ``query_by_target``：``WHERE target_type=? AND target_id=? ORDER BY created_at DESC``。
   原本 ``target_id`` 单列索引选择性低（多个 target_type 共享同一 id 空间），
   复合索引让定位精确。左前缀也覆盖纯 ``target_id`` 查询。

2. ``ix_audit_logs_created_at`` — 服务 ``query_all``：
   ``ORDER BY created_at DESC LIMIT/OFFSET``。原来无索引，分页深翻需要
   全表排序；带索引后可走 index scan + limit。

降级路径：两个索引都是新增，downgrade 直接 drop。原 ``target_id`` 单列索引
保持原状（migration 历史中已建），不重复删除以避免破坏其他可能依赖它的查询。

Revision ID: 029
Revises: 028
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(inspector, table: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audit_logs" not in set(inspector.get_table_names()):
        return

    if not _has_index(inspector, "audit_logs", "ix_audit_logs_target"):
        op.create_index(
            "ix_audit_logs_target",
            "audit_logs",
            ["target_type", "target_id"],
        )
    if not _has_index(inspector, "audit_logs", "ix_audit_logs_created_at"):
        op.create_index(
            "ix_audit_logs_created_at",
            "audit_logs",
            ["created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "audit_logs" not in set(inspector.get_table_names()):
        return

    if _has_index(inspector, "audit_logs", "ix_audit_logs_created_at"):
        op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    if _has_index(inspector, "audit_logs", "ix_audit_logs_target"):
        op.drop_index("ix_audit_logs_target", table_name="audit_logs")
