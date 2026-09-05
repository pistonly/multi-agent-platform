"""Sync --check 固定契约与五类 kind 判定（实验 M2 A2 验收）。

sync --check 是 FS 内容平面与 server 投影 cache 之间的对账入口：
对每个实验 slug，根据两侧 ``FsExperimentRead`` 是否齐全与字段是否一致，
归类到下列五种 kind（与 review plan-review.yaml reasonable_items 中
host 审过的五类枚举一致）：

- ``aligned``：两侧都在 + 字段全部一致（不 blocking）
- ``fs_only_terminal``：仅 FS 在 + FS phase ∈ terminal（done/cancelled）
  —— A4 允许的 lazy 展示场景（不 blocking）
- ``db_only``：仅 DB 在（不 blocking 仅在 DB phase ∈ terminal；
  非 terminal → blocking，意味着 FS 丢了活跃实验）
- ``divergent``：两侧都在但字段不一致（始终 blocking）
- ``invalid``：FS phase 非法 / 缺关键字段 / 字段类型错误等（始终 blocking）

A2 acceptance 一致要求：**aligned 且无 blocking 才能作为切换证据**——
本模块只负责判定与产出固定 schema，不做切换决策（切换由 host 在实验
I4 / I5 中控）。

公开 API：

- 类型：``SyncCheckKind`` / ``SyncFieldDiff`` / ``SyncCheckItem`` /
  ``SyncCheckReport``
- 判定：``classify_experiment_sync`` / ``run_sync_check``（纯函数；
  无 IO，可由 CLI/server/单测共享）

历史兼容：``map sync check`` CLI 旧实现是 ``fs_status``（reachability
handshake），A2 之后落地为本模块 ``run_sync_check``，CLI 是其包装层。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from map_types.schemas.canonical import metadata_hash

# 实验 phase 中视为「终态」的可丢失集合（FS 缺失不算 blocking）。
# 与 ``map_types.enums.ExperimentPhase`` 对齐：done / cancelled 之外
# 还有 result_review（终态但仍要 FS 有索引，否则 round/lock 状态丢失）。
TERMINAL_PHASES: frozenset[str] = frozenset({"done", "cancelled"})

# 五类对账结果
SYNC_CHECK_KIND_VALUES: tuple[str, ...] = (
    "aligned",
    "fs_only_terminal",
    "db_only",
    "divergent",
    "invalid",
)

# 真正进入 sync check 比较的字段集（A2 fixed contract 的「权威字段矩阵」）。
# 与 ``FsExperimentRead`` 补全的 5 字段对齐：title / phase / description /
# executor / topic / current_plan_version（dir_path / *_path / created_at /
# updated_at / projection_id 不参与对齐判定 —— 路径是源位置，时间戳可能
# 不同侧由不同 watcher 写入；projection_id 仅校验不自动改写——见 plan A1）。
ALIGN_FIELDS: tuple[str, ...] = (
    "title",
    "phase",
    "description",
    "creator",
    "executor",
    "topic",
    "current_plan_version",
)


class SyncCheckKind(str, Enum):
    """A2 sync --check 五类 kind 枚举。"""

    ALIGNED = "aligned"
    FS_ONLY_TERMINAL = "fs_only_terminal"
    DB_ONLY = "db_only"
    DIVERGENT = "divergent"
    INVALID = "invalid"


class SyncFieldDiff(BaseModel):
    """单个字段的不一致点（kind=divergent 时填充）。"""

    field: str
    fs_value: object = None
    db_value: object = None


class SyncCheckItem(BaseModel):
    """单个实验的对账结果。"""

    experiment_slug: str
    kind: SyncCheckKind
    blocking: bool
    field_diffs: list[SyncFieldDiff] = Field(default_factory=list)
    fs_hash: str | None = None
    db_hash: str | None = None
    message: str = ""


class SyncCheckReport(BaseModel):
    """sync --check 整体报告（A2 fixed schema）。"""

    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    total: int = 0
    aligned: int = 0
    fs_only_terminal: int = 0
    db_only: int = 0
    divergent: int = 0
    invalid: int = 0
    blocking_count: int = 0
    items: list[SyncCheckItem] = Field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """A2 切换门槛：``blocking_count == 0`` 且无 ``invalid``。"""
        return self.blocking_count == 0 and self.invalid == 0


# ---------------------------------------------------------------------------
# 判定逻辑（纯函数）
# ---------------------------------------------------------------------------


def _experiment_id_of(experiment: object) -> uuid.UUID:
    """统一从 ``FsExperimentRead``（或 duck-typed 同形对象）取 id。"""
    return uuid.UUID(str(experiment.id))


def _is_terminal(phase: str | None) -> bool:
    return phase in TERMINAL_PHASES


def _field_value(experiment: object, field: str) -> object:
    return getattr(experiment, field)


def classify_experiment_sync(
    *,
    slug: str,
    fs: object | None,
    db: object | None,
) -> SyncCheckItem:
    """对单个实验按两侧存在性 + 字段一致性归类。

    参数 duck-typed：传 ``FsExperimentRead`` / ``FsExperiment`` / 任何
    暴露 ``ALIGN_FIELDS`` + ``id`` 属性的对象均可；这让分类函数可在
    CLI（FsExperiment）/ server（projection cache 反序列化）/ 单元测试
    之间共享。

    注意：``fs_hash`` / ``db_hash`` 仅在对象是 ``FsExperimentRead`` 时
    计算（duck-typed 对象可能没有 ``model_dump``）。
    """
    from map_types.schemas.fs import FsExperimentRead

    fs_hash = (
        metadata_hash(fs) if isinstance(fs, FsExperimentRead) and fs is not None else None
    )
    db_hash = (
        metadata_hash(db) if isinstance(db, FsExperimentRead) and db is not None else None
    )

    if fs is None and db is None:
        return SyncCheckItem(
            experiment_slug=slug,
            kind=SyncCheckKind.INVALID,
            blocking=True,
            message="both fs and db missing",
        )

    if fs is None:
        # db_only
        db_phase = _field_value(db, "phase") if db is not None else None
        blocking = not _is_terminal(db_phase)
        msg = (
            "fs missing for active db experiment"
            if blocking
            else "db_only (terminal)"
        )
        return SyncCheckItem(
            experiment_slug=slug,
            kind=SyncCheckKind.DB_ONLY,
            blocking=blocking,
            db_hash=db_hash,
            message=msg,
        )

    if db is None:
        # fs_only → terminal vs blocking
        fs_phase = _field_value(fs, "phase") if fs is not None else None
        if _is_terminal(fs_phase):
            return SyncCheckItem(
                experiment_slug=slug,
                kind=SyncCheckKind.FS_ONLY_TERMINAL,
                blocking=False,
                fs_hash=fs_hash,
                message="fs_only (terminal)",
            )
        # phase 非法也算 invalid
        if fs_phase not in {"draft", "review", "approved", "running", "result_review"}:
            return SyncCheckItem(
                experiment_slug=slug,
                kind=SyncCheckKind.INVALID,
                blocking=True,
                fs_hash=fs_hash,
                message=f"invalid phase: {fs_phase!r}",
            )
        return SyncCheckItem(
            experiment_slug=slug,
            kind=SyncCheckKind.DIVERGENT,
            blocking=True,
            fs_hash=fs_hash,
            message="fs_only (active; db missing) — fs must reach db",
        )

    # 两边都在 → 字段对齐
    diffs: list[SyncFieldDiff] = []
    for f in ALIGN_FIELDS:
        fs_v = _field_value(fs, f)
        db_v = _field_value(db, f)
        if fs_v != db_v:
            diffs.append(SyncFieldDiff(field=f, fs_value=fs_v, db_value=db_v))
    if diffs:
        return SyncCheckItem(
            experiment_slug=slug,
            kind=SyncCheckKind.DIVERGENT,
            blocking=True,
            field_diffs=diffs,
            fs_hash=fs_hash,
            db_hash=db_hash,
            message=f"{len(diffs)} field(s) differ",
        )
    return SyncCheckItem(
        experiment_slug=slug,
        kind=SyncCheckKind.ALIGNED,
        blocking=False,
        fs_hash=fs_hash,
        db_hash=db_hash,
        message="aligned",
    )


def run_sync_check(
    fs_experiments: list[object],
    db_experiments: list[object],
) -> SyncCheckReport:
    """聚合：对 ``fs_experiments`` ∪ ``db_experiments`` slug 集合逐个判定。

    两边列表允许重复 slug —— 重复会进入 ``divergent``（同一 slug 多条
    实验记录本身就是脏数据；此处只比对首条，更细的归并留给 sync publish
    / 一致性巡检）。
    """
    by_slug_fs: dict[str, object] = {}
    for e in fs_experiments:
        by_slug_fs[str(e.slug)] = e
    by_slug_db: dict[str, object] = {}
    for e in db_experiments:
        by_slug_db[str(e.slug)] = e

    slugs = sorted(set(by_slug_fs) | set(by_slug_db))
    report = SyncCheckReport(total=len(slugs))
    for slug in slugs:
        item = classify_experiment_sync(
            slug=slug,
            fs=by_slug_fs.get(slug),
            db=by_slug_db.get(slug),
        )
        report.items.append(item)
        if item.kind == SyncCheckKind.ALIGNED:
            report.aligned += 1
        elif item.kind == SyncCheckKind.FS_ONLY_TERMINAL:
            report.fs_only_terminal += 1
        elif item.kind == SyncCheckKind.DB_ONLY:
            report.db_only += 1
        elif item.kind == SyncCheckKind.DIVERGENT:
            report.divergent += 1
        elif item.kind == SyncCheckKind.INVALID:
            report.invalid += 1
        if item.blocking:
            report.blocking_count += 1
    return report


__all__ = [
    "ALIGN_FIELDS",
    "TERMINAL_PHASES",
    "SYNC_CHECK_KIND_VALUES",
    "SyncCheckKind",
    "SyncFieldDiff",
    "SyncCheckItem",
    "SyncCheckReport",
    "classify_experiment_sync",
    "run_sync_check",
]
