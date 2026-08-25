"""notifications composite query index + redundant single-column cleanup

T11（2026-08 认证/通知优化批次）：``notification_service.list_for_agent``
的排序分页路径是 ``WHERE recipient_agent_id = ? ORDER BY updated_at DESC``
（Web 通知列表 + waker 轮询均命中），但表上只有 ``recipient_agent_id``
单列索引——大表需要先按 recipient 取回全部行再 filesort。

1. ``ix_notifications_recipient_updated(recipient_agent_id, updated_at)``
   — 复合索引消除 filesort；左前缀同时覆盖纯 ``recipient_agent_id``
   等值查询（count_unread / mark_all_read / mention 同步批量 UPDATE）。

2. 裁剪评估（依据 server/ 内全部查询点位的 grep 审计）：
   - ``ix_notifications_fingerprint_version`` — 无任何查询过滤该列，
     基数恒为 2（v1/v2），纯写放大 → 删。
   - ``ix_notifications_category`` — 仅与 recipient_agent_id 联合过滤，
     从不独立出现，基数约 3，联合查询由新复合索引左前缀服务 → 删。
   - ``ix_notifications_read_at`` — 仅与 recipient_agent_id 联合过滤
     （read_at IS NULL），从不独立出现 → 删。
   - ``ix_notifications_group_key`` — 无独立查询；upsert 的
     ``ON CONFLICT (recipient_agent_id, group_key)`` 走 038 的 UNIQUE
     约束索引，该单列索引自 038 起即冗余 → 删。
   - ``ix_notifications_recipient_agent_id`` — 新复合索引左前缀完全
     覆盖 → 删。
   - ``ix_notifications_created_at`` — 保留：ORDER BY updated_at 的
     tiebreaker，且运维手工按时间排查可能用到，不在本次裁剪范围。

3. 漂移修复：ORM 模型对 ``project_id`` 声明了 ``index=True``，但历史
   迁移从未在数据库里创建过 ``ix_notifications_project_id``——迁移库
   与 ``create_all`` 库（dev/test）一直不一致。本迁移一并补建（幂等
   guard），让两侧索引集重新对齐。

ORM 模型同步（``server/domain/models.py``）：对应列的 ``index=True``
移除、``__table_args__`` 声明复合索引——保证 ``create_all``（dev/test）
与迁移库的索引集一致。

降级路径：重建上述 5 个单列索引、删除复合索引与补建的 project_id
索引（若确系本迁移所建），恢复 051 之前的索引布局。

Revision ID: 052
Revises: 051
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "notifications"
_COMPOSITE = "ix_notifications_recipient_updated"
_PROJECT_INDEX = "ix_notifications_project_id"

# 裁剪的单列索引 → 重建列（downgrade 用）。
_DROPPED_SINGLE: dict[str, list[str]] = {
    "ix_notifications_recipient_agent_id": ["recipient_agent_id"],
    "ix_notifications_read_at": ["read_at"],
    "ix_notifications_category": ["category"],
    "ix_notifications_group_key": ["group_key"],
    "ix_notifications_fingerprint_version": ["fingerprint_version"],
}


def _has_index(inspector: sa.Inspector, name: str) -> bool:
    return any(idx["name"] == name for idx in inspector.get_indexes(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if _TABLE not in set(inspector.get_table_names()):
        return

    if not _has_index(inspector, _COMPOSITE):
        op.create_index(_COMPOSITE, _TABLE, ["recipient_agent_id", "updated_at"])
    for name in _DROPPED_SINGLE:
        if _has_index(inspector, name):
            op.drop_index(name, table_name=_TABLE)
    # 漂移修复：迁移库从未建过 ORM 声明的 project_id 索引，补齐。
    if not _has_index(inspector, _PROJECT_INDEX):
        op.create_index(_PROJECT_INDEX, _TABLE, ["project_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if _TABLE not in set(inspector.get_table_names()):
        return

    for name, columns in _DROPPED_SINGLE.items():
        if not _has_index(inspector, name):
            op.create_index(name, _TABLE, columns)
    if _has_index(inspector, _PROJECT_INDEX):
        op.drop_index(_PROJECT_INDEX, table_name=_TABLE)
    if _has_index(inspector, _COMPOSITE):
        op.drop_index(_COMPOSITE, table_name=_TABLE)
