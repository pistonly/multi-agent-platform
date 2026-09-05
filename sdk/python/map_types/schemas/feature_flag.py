"""Project feature flag SDK schema (实验 M2 I4：A4).

读面 + set 请求 schema。返回 ``ProjectFeatureFlagRead`` 带元信息
（set_by_agent_id / set_at / reason），让 CLI 在 ``list`` 输出里直接
显示「最近谁 / 何时 / 为什么」。

公开 API（re-export via ``map_types.schemas.__init__``）：

- :class:`ProjectFeatureFlagRead` —— 读面
- :class:`ProjectFeatureFlagSet` —— set 请求（key + value + reason）

``key`` 字段用枚举字面量约束（当前只一个 ``fs_stop_duplicate_insert``），
新增 flag 时改这里 + ``feature_flag_service.REGISTERED_FLAGS``。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from .base import ORMModel

# 单源常量：SDK 与 server service 共享。
FLAG_FS_STOP_DUPLICATE_INSERT = "fs_stop_duplicate_insert"

# 受支持 flag_key 字面量。新增 flag 时这里加一行 + 服务端
# ``feature_flag_service.REGISTERED_FLAGS`` 加一项。
FlagKey = Literal[
    "fs_stop_duplicate_insert",
]


class ProjectFeatureFlagRead(ORMModel):
    """读面：值 + 最近 flip 的 actor / 时间 / reason。"""

    project_id: uuid.UUID
    flag_key: FlagKey
    flag_value: str
    set_by_agent_id: uuid.UUID
    set_at: datetime
    reason: str | None = None


class ProjectFeatureFlagSet(ORMModel):
    """set 请求：flip flag 到 value（reason 在 ON flip 时必填非空）。"""

    flag_value: Literal["on", "off"]
    reason: str | None = None


__all__ = [
    "FLAG_FS_STOP_DUPLICATE_INSERT",
    "FlagKey",
    "ProjectFeatureFlagRead",
    "ProjectFeatureFlagSet",
]
