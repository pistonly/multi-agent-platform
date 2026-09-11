"""Project-level feature flag 服务（实验 M2 I4：A4）。

单一标志位表 + 三类操作：read（get / list_all）/ write（set）/ 语义解释
（kill switch / fail-closed gate）。flag key 注册在 ``REGISTERED_FLAGS``
白名单，未注册的 key set 时抛 ``UnknownFlagKeyError``（防止 caller 拼
写错「fs_stop_duplicate_insert」变成无人读的孤行）。

设计要点：

- **value 校验** —— registered flag 各自有合法 value 集合；非法 value
  抛 ``InvalidFlagValueError``，避免「on」/「true」/「1」/「enabled」四种
  写法并存。
- **actor 权限** —— set 必须是 host creator 或 admin；非授权 actor 抛
  ``PermissionError``（沿用 server 既有 ``ForbiddenError``）。CLI 在写入
  前显式校验（双重 gate）。
- **set 是幂等的 upsert** —— 同一个 flag_key 在同一 project 上多次 set
  会覆盖值与 reason；``set_by_agent_id`` / ``set_at`` 同步刷新。这是有
  意的（kill switch 需要 fast flip 能力）。
- **read 不强制白名单** —— 读未注册的 key 直接返回 None（不存在），不
  抛错，方便外部代码兜底默认行为。

触发条件与触发人（reviewer minor 建议 I4 落点）：

- ``fs_stop_duplicate_insert=on`` 的合法触发人：**host creator 或 admin**
  （其他 persona 写 ``map project config flag set`` 直接 403）。
- 合法触发条件（写 reason 必须命中其一；CLI 不强制枚举，audit 由 host
  决定）：
  - 「A2 验收达标 + A3 race 收敛 + 灰度内活跃项目推进 M2」
  - 「A4 fail-closed 单测 + 远程 e2e 实战覆盖到位」
  - 反向触发（``off``/kill switch）：「线上 duplicate-INSERT 引发的
    FS/DB 漂移影响 list/sort/通知；先回退到 M1 双写保 list 稳态」。
- kill switch 触发后保留：FS 文件（``map/experiments/<slug>/index.md`` /
  ``plan.md`` / ``log.md`` / ``reviews/*.yaml``）；projection revision
  与 last-known-good 不重置；``fs_stop_duplicate_insert`` 行本身保留
  （``flag_value=off``）作为审计锚点。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    ProjectFeatureFlag,
)
from server.services.errors import ForbiddenError

if TYPE_CHECKING:
    pass

# Flag key 常量（代码引用单源，避免拼写漂移）
FLAG_FS_STOP_DUPLICATE_INSERT = "fs_stop_duplicate_insert"
FLAG_TOPIC_DB_READ_RETIRED = "topic_db_read_retired"

# 合法 value 集合。``on`` / ``off`` 是当前仅有的两个；未来加 ``phase1``
# / ``phase2`` 等灰度值时只改这里。
_FS_STOP_DUPLICATE_INSERT_VALUES: frozenset[str] = frozenset({"on", "off"})
_TOPIC_DB_READ_RETIRED_VALUES: frozenset[str] = frozenset({"on", "off"})

# ON flip 强制非空 reason 的 flag 集合（单向门决策必须留审计锚点；
# OFF flip 一律允许空 reason —— fast rollback 不该被空文本阻塞）。
_ON_FLIP_REQUIRES_REASON: frozenset[str] = frozenset(
    {FLAG_FS_STOP_DUPLICATE_INSERT, FLAG_TOPIC_DB_READ_RETIRED}
)


@dataclass(frozen=True)
class FlagSpec:
    """注册表里一条 flag 的元信息（key + 合法 value + 语义说明）。"""

    key: str
    allowed_values: frozenset[str]
    description: str


REGISTERED_FLAGS: dict[str, FlagSpec] = {
    FLAG_FS_STOP_DUPLICATE_INSERT: FlagSpec(
        key=FLAG_FS_STOP_DUPLICATE_INSERT,
        allowed_values=_FS_STOP_DUPLICATE_INSERT_VALUES,
        description=(
            "实验 M2 A4：M2 D3/D4 分阶段切换 + kill switch + fail closed。"
            "ON：lifecycle 写路径对 active 实验缺 projection 主行 → "
            "fail closed（不让 lazy materialization 放行）；OFF（M1 默认）："
            "行为不变，可恢复双写。kill switch 触发条件与触发人见"
            " feature_flag_service 模块 docstring。"
        ),
    ),
    FLAG_TOPIC_DB_READ_RETIRED: FlagSpec(
        key=FLAG_TOPIC_DB_READ_RETIRED,
        allowed_values=_TOPIC_DB_READ_RETIRED_VALUES,
        description=(
            "实验 0f271f7e A5：内容侧 DB 话题读路径退役总开关。ON："
            "/topics 列表只返回 map/ FS 段；GET topic/comments 在 FS "
            "miss 且 DB 行存在时 410 引导 `map topic migrate`；PATCH "
            "archived 410 引导 `map topic archive`（故翻 ON 前必须先跑完"
            " map topic migrate 收尾）。OFF（默认）：读路径与 v0.13 M58"
            " 行为逐字节一致（DB fallback 保留）。触发人：host persona "
            "或 admin；ON flip 强制非空 reason。"
        ),
    ),
}


class UnknownFlagKeyError(ValueError):
    """set / list 调用了未注册的 flag_key。"""

    def __init__(self, key: str) -> None:
        super().__init__(f"unknown project feature flag key: {key!r}")
        self.key = key


class InvalidFlagValueError(ValueError):
    """set 调用了 registered flag 的非合法 value。"""

    def __init__(self, key: str, value: str, allowed: Iterable[str]) -> None:
        allowed_list = sorted(allowed)
        super().__init__(
            f"invalid flag value for {key!r}: {value!r}; "
            f"allowed values: {allowed_list}"
        )
        self.key = key
        self.value = value
        self.allowed = tuple(allowed_list)


@dataclass(frozen=True)
class ProjectFeatureFlagRead:
    """读面返回：值 + 元信息（最近 flip 的 actor / reason / 时间）。"""

    project_id: uuid.UUID
    flag_key: str
    flag_value: str
    set_by_agent_id: uuid.UUID
    set_at: datetime
    reason: str | None

    @classmethod
    def from_orm(cls, row: ProjectFeatureFlag) -> ProjectFeatureFlagRead:
        return cls(
            project_id=row.project_id,
            flag_key=row.flag_key,
            flag_value=row.flag_value,
            set_by_agent_id=row.set_by_agent_id,
            set_at=row.set_at,
            reason=row.reason,
        )


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def _ensure_can_set_flag(actor: Agent, project_id: uuid.UUID) -> None:
    """仅 host creator 或 admin 可 set（reviewer minor 触发人约束）。

    host creator 限定为「该 project 的 host persona agent」（不是任何
    agent role==host）。这一点与 ``experiment_capabilities_service`` 的
    host 判定保持一致。
    """
    from map_types.persona import persona_from_agent_name

    if actor.role.value == "admin":
        return
    if actor.project_id != project_id:
        # 非本项目 agent → 视为无权限（即便 role=host 也只能管自己项目）
        raise ForbiddenError(
            f"flag set requires project host or admin; actor "
            f"{actor.id} belongs to project {actor.project_id}, "
            f"target project is {project_id}"
        )
    persona = persona_from_agent_name(actor.name)
    if persona != "host":
        raise ForbiddenError(
            f"flag set requires project host or admin; actor {actor.id} "
            f"persona={persona!r}"
        )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_flag(
    db: Session, project_id: uuid.UUID, flag_key: str
) -> ProjectFeatureFlagRead | None:
    """读单条 flag；不存在返 None（不要 raise — caller 走 default）。"""
    row = db.get(ProjectFeatureFlag, (project_id, flag_key))
    if row is None:
        return None
    return ProjectFeatureFlagRead.from_orm(row)


def list_flags(
    db: Session, project_id: uuid.UUID
) -> list[ProjectFeatureFlagRead]:
    """列 project 下所有已 set 的 flag；空 list 表示一行都没有（全 default）。"""
    rows = db.scalars(
        select(ProjectFeatureFlag)
        .where(ProjectFeatureFlag.project_id == project_id)
        .order_by(ProjectFeatureFlag.flag_key)
    ).all()
    return [ProjectFeatureFlagRead.from_orm(row) for row in rows]


def set_flag(
    db: Session,
    *,
    project_id: uuid.UUID,
    flag_key: str,
    flag_value: str,
    actor: Agent,
    reason: str | None = None,
    commit: bool = True,
) -> ProjectFeatureFlagRead:
    """upsert 一条 flag（reviewer minor：actor 必须是 host creator / admin）。

    - 未注册的 key → ``UnknownFlagKeyError``
    - 非合法 value → ``InvalidFlagValueError``
    - actor 不是 host creator / admin → ``ForbiddenError``
    - reason 在「flag_value=on 且 flag 是 fs_stop_duplicate_insert」时
      **强制非空**（kill switch / fail-closed gate flip 必须有审计锚点；
      off flip 也鼓励写 reason 但不强拦——kill switch 是 fast path，
      不应该被空 reason 阻塞）。
    """
    spec = REGISTERED_FLAGS.get(flag_key)
    if spec is None:
        raise UnknownFlagKeyError(flag_key)
    if flag_value not in spec.allowed_values:
        raise InvalidFlagValueError(flag_key, flag_value, spec.allowed_values)
    _ensure_can_set_flag(actor, project_id)

    # ON flip 强制 reason 非空（单向门 / kill switch 关键决策不留无审计
    # 空白）；OFF flip 允许空 reason（fast rollback 不该被空文本阻塞
    # ——off 本身就是审计锚）。
    if (
        flag_key in _ON_FLIP_REQUIRES_REASON
        and flag_value == "on"
        and not (reason and reason.strip())
    ):
        raise ValueError(
            f"setting {flag_key}=on requires a non-empty reason "
            "(audit anchor for the one-way-door flip)"
        )

    row = db.get(ProjectFeatureFlag, (project_id, flag_key))
    if row is None:
        row = ProjectFeatureFlag(
            project_id=project_id,
            flag_key=flag_key,
            flag_value=flag_value,
            set_by_agent_id=actor.id,
            reason=reason,
        )
        db.add(row)
    else:
        row.flag_value = flag_value
        row.set_by_agent_id = actor.id
        row.reason = reason
        # set_at 由 DB onupdate 刷新
    if commit:
        db.commit()
        db.refresh(row)
    return ProjectFeatureFlagRead.from_orm(row)


# ---------------------------------------------------------------------------
# 便捷查询
# ---------------------------------------------------------------------------


def is_fs_stop_duplicate_insert_on(db: Session, project_id: uuid.UUID) -> bool:
    """快捷读：``fs_stop_duplicate_insert`` 是否为 ``on``。

    集中在这里避免 ``fs_stop_duplicate_insert`` 字符串在多个调用点
    散落（reviewer minor 触发条件集中表达）。None / off / 任何其他值
    都视为 False（保守默认：fail-closed 默认不激活）。
    """
    flag = get_flag(db, project_id, FLAG_FS_STOP_DUPLICATE_INSERT)
    return flag is not None and flag.flag_value == "on"


def is_topic_db_read_retired_on(db: Session, project_id: uuid.UUID) -> bool:
    """快捷读：``topic_db_read_retired`` 是否为 ``on``。

    None / off / 任何其他值都视为 False（保守默认：DB 读路径保持
    v0.13 M58 行为，DB fallback 不退役）。gate 点在 ``server/api/topics.py``
    的 list / get / comments / PATCH-archived 四个读归档面。
    """
    flag = get_flag(db, project_id, FLAG_TOPIC_DB_READ_RETIRED)
    return flag is not None and flag.flag_value == "on"
