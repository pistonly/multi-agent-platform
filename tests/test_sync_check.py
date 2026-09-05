"""Sync --check 固定契约与五类 kind 判定（实验 M2 A2）单测。

覆盖：

- 五类 kind 各自一条主路径（aligned / fs_only_terminal / db_only /
  divergent / invalid）
- ``fs_only`` 活跃态 → divergent（blocking，fs 必须先 publish）
- ``db_only`` 活跃态 → db_only + blocking（fs 丢数据）
- ``fs_only`` 终态 → fs_only_terminal（非 blocking）
- ``ALIGN_FIELDS`` 之外字段变化 → 不影响 kind（dir_path 不参与）
- ``run_sync_check`` 聚合计数 + blocking_count
- 连续两次独立快照输出相同（determinism — A2 acceptance 要求）
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from map_types.schemas.canonical import metadata_hash
from map_types.schemas.fs import FsExperimentRead
from map_types.schemas.sync_check import (
    ALIGN_FIELDS,
    SYNC_CHECK_KIND_VALUES,
    TERMINAL_PHASES,
    SyncCheckKind,
    classify_experiment_sync,
    run_sync_check,
)


def _exp(
    *,
    slug: str,
    phase: str = "running",
    title: str | None = None,
    description: str = "",
    creator: str = "host",
    executor: str = "participant",
    topic: str = "demo-topic",
    current_plan_version: int = 1,
) -> FsExperimentRead:
    return FsExperimentRead(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, f"map.test.{slug}"),
        slug=slug,
        title=title if title is not None else slug.replace("-", " ").title(),
        description=description,
        phase=phase,
        creator=creator,
        dir_path=f"map/experiments/{slug}",
        plan_path=f"map/experiments/{slug}/plan.md",
        log_path=f"map/experiments/{slug}/log.md",
        review_path=None,
        current_plan_version=current_plan_version,
        executor=executor,
        topic=topic,
        updated_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        projection_id=None,
    )


# ---------------------------------------------------------------------------
# Kind 枚举契约
# ---------------------------------------------------------------------------


class TestSyncCheckKindEnum:
    def test_kind_values_match_plan(self):
        assert SYNC_CHECK_KIND_VALUES == (
            "aligned",
            "fs_only_terminal",
            "db_only",
            "divergent",
            "invalid",
        )
        assert {k.value for k in SyncCheckKind} == set(SYNC_CHECK_KIND_VALUES)

    def test_terminal_phases_set(self):
        """done / cancelled 才算 terminal；result_review 不算（A4 lazy 不放）。"""
        assert frozenset({"done", "cancelled"}) == TERMINAL_PHASES


# ---------------------------------------------------------------------------
# 分类逻辑
# ---------------------------------------------------------------------------


class TestClassifyExperimentSync:
    def test_aligned(self):
        e = _exp(slug="a", phase="running")
        item = classify_experiment_sync(slug="a", fs=e, db=e)
        assert item.kind == SyncCheckKind.ALIGNED
        assert item.blocking is False
        assert item.field_diffs == []
        assert item.fs_hash == item.db_hash == metadata_hash(e)

    def test_fs_only_terminal(self):
        e = _exp(slug="done1", phase="done")
        item = classify_experiment_sync(slug="done1", fs=e, db=None)
        assert item.kind == SyncCheckKind.FS_ONLY_TERMINAL
        assert item.blocking is False
        assert item.fs_hash == metadata_hash(e)
        assert item.db_hash is None

    def test_fs_only_cancelled_terminal(self):
        e = _exp(slug="c1", phase="cancelled")
        item = classify_experiment_sync(slug="c1", fs=e, db=None)
        assert item.kind == SyncCheckKind.FS_ONLY_TERMINAL
        assert item.blocking is False

    def test_fs_only_active_is_divergent_blocking(self):
        e = _exp(slug="active1", phase="running")
        item = classify_experiment_sync(slug="active1", fs=e, db=None)
        assert item.kind == SyncCheckKind.DIVERGENT
        assert item.blocking is True
        assert "fs_only" in item.message

    def test_fs_only_invalid_phase(self):
        e = _exp(slug="bad1", phase="not-a-real-phase")
        item = classify_experiment_sync(slug="bad1", fs=e, db=None)
        assert item.kind == SyncCheckKind.INVALID
        assert item.blocking is True
        assert "invalid phase" in item.message

    def test_db_only_terminal_not_blocking(self):
        e = _exp(slug="dt", phase="done")
        item = classify_experiment_sync(slug="dt", fs=None, db=e)
        assert item.kind == SyncCheckKind.DB_ONLY
        assert item.blocking is False

    def test_db_only_active_blocking(self):
        e = _exp(slug="da", phase="running")
        item = classify_experiment_sync(slug="da", fs=None, db=e)
        assert item.kind == SyncCheckKind.DB_ONLY
        assert item.blocking is True
        assert "fs missing for active" in item.message

    def test_both_missing_is_invalid(self):
        item = classify_experiment_sync(slug="ghost", fs=None, db=None)
        assert item.kind == SyncCheckKind.INVALID
        assert item.blocking is True

    def test_divergent_field_mismatch(self):
        fs = _exp(slug="x", phase="running", title="A")
        db = _exp(slug="x", phase="running", title="B")
        item = classify_experiment_sync(slug="x", fs=fs, db=db)
        assert item.kind == SyncCheckKind.DIVERGENT
        assert item.blocking is True
        diff_fields = {d.field for d in item.field_diffs}
        assert diff_fields == {"title"}

    def test_divergent_multiple_fields(self):
        fs = _exp(slug="y", phase="running", title="A", description="x")
        db = _exp(slug="y", phase="running", title="B", description="y")
        item = classify_experiment_sync(slug="y", fs=fs, db=db)
        assert item.kind == SyncCheckKind.DIVERGENT
        diff_fields = {d.field for d in item.field_diffs}
        assert "title" in diff_fields and "description" in diff_fields

    def test_align_fields_excludes_dir_path_and_paths(self):
        """dir_path / *_path / created_at / updated_at 不参与对齐判定。"""
        fs = _exp(slug="z")
        db = _exp(slug="z")
        # Modify fields that should NOT affect alignment via a tiny adapter.
        from dataclasses import dataclass

        @dataclass
        class _Adap:
            id: object
            slug: str
            title: str
            description: str
            phase: str
            creator: str
            current_plan_version: int
            executor: str
            topic: str

        a = _Adap(
            id=fs.id,
            slug=fs.slug,
            title=fs.title,
            description=fs.description,
            phase=fs.phase,
            creator=fs.creator,
            current_plan_version=fs.current_plan_version,
            executor=fs.executor,
            topic=fs.topic,
        )
        item = classify_experiment_sync(slug="z", fs=a, db=db)
        assert item.kind == SyncCheckKind.ALIGNED

    def test_align_fields_constant(self):
        """ALIGN_FIELDS 锁死：增删字段会破坏 schema 兼容性。"""
        assert set(ALIGN_FIELDS) == {
            "title",
            "phase",
            "description",
            "creator",
            "executor",
            "topic",
            "current_plan_version",
        }


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


class TestRunSyncCheck:
    def test_aggregates_counts(self):
        a = _exp(slug="a", phase="done")
        b = _exp(slug="b", phase="running")
        c = _exp(slug="c", phase="done")
        d = _exp(slug="d", phase="done")
        # FS has a (done) + b (active)
        # DB has c (done, db_only terminal) + a (aligned)
        fs_list = [a, b]
        db_list = [a, c, d]  # d is also db_only terminal
        report = run_sync_check(fs_list, db_list)

        assert report.total == 4  # a, b, c, d
        assert report.aligned == 1
        assert report.fs_only_terminal == 0
        assert report.db_only == 2  # c, d (both terminal)
        assert report.divergent == 1  # b (fs_only active)
        assert report.invalid == 0
        assert report.blocking_count == 1
        assert report.is_clean is False  # b is blocking

    def test_clean_when_no_blocking(self):
        a = _exp(slug="a", phase="done")
        b = _exp(slug="b", phase="cancelled")
        fs_list = [a, b]
        db_list = [a, b]
        report = run_sync_check(fs_list, db_list)
        assert report.aligned == 2
        assert report.blocking_count == 0
        assert report.is_clean is True

    def test_determinism_two_independent_runs(self):
        """A2 acceptance: 连续两次独立快照输出相同 → no blocking drift。"""
        fs_list = [
            _exp(slug="x", phase="done"),
            _exp(slug="y", phase="running"),
            _exp(slug="z", phase="done"),
        ]
        db_list = [
            _exp(slug="x", phase="done"),
            _exp(slug="z", phase="done"),
            _exp(slug="w", phase="cancelled"),  # db_only terminal
        ]
        r1 = run_sync_check(fs_list, db_list)
        r2 = run_sync_check(fs_list, db_list)
        # 不比较 generated_at（时间戳），其余字段逐项相等
        assert r1.total == r2.total
        assert r1.aligned == r2.aligned
        assert r1.fs_only_terminal == r2.fs_only_terminal
        assert r1.db_only == r2.db_only
        assert r1.divergent == r2.divergent
        assert r1.invalid == r2.invalid
        assert r1.blocking_count == r2.blocking_count
        assert [
            (it.experiment_slug, it.kind.value, it.blocking) for it in r1.items
        ] == [
            (it.experiment_slug, it.kind.value, it.blocking) for it in r2.items
        ]

    def test_empty_inputs(self):
        report = run_sync_check([], [])
        assert report.total == 0
        assert report.blocking_count == 0
        assert report.is_clean is True
        assert report.items == []


# ---------------------------------------------------------------------------
# FsExperimentRead 5 字段补全（I0 §A.4 标记项）
# ---------------------------------------------------------------------------


class TestFsExperimentReadBackfill:
    def test_default_construction_includes_5_new_fields(self):
        e = FsExperimentRead(
            id=uuid.uuid4(),
            slug="x",
            title="x",
            creator="host",
            dir_path="map/experiments/x",
        )
        assert e.current_plan_version == 1
        assert e.executor == ""
        assert e.topic == ""
        assert e.updated_at is None
        assert e.projection_id is None

    def test_full_construction_round_trip(self):
        eid = uuid.uuid4()
        pid = uuid.uuid4()
        when = datetime(2026, 9, 5, tzinfo=timezone.utc)
        e = FsExperimentRead(
            id=eid,
            slug="x",
            title="x",
            creator="host",
            dir_path="map/experiments/x",
            current_plan_version=3,
            executor="participant",
            topic="exp-m2-design",
            updated_at=when,
            projection_id=pid,
        )
        assert e.current_plan_version == 3
        assert e.executor == "participant"
        assert e.topic == "exp-m2-design"
        assert e.updated_at == when
        assert e.projection_id == pid

    def test_metadata_hash_includes_new_fields(self):
        """5 字段任一变化 → metadata_hash 变化（A1 权威矩阵）。"""
        base = FsExperimentRead(
            id=uuid.uuid4(),
            slug="x",
            title="x",
            creator="host",
            dir_path="map/experiments/x",
        )
        h_base = metadata_hash(base)

        h_executor = metadata_hash(base.model_copy(update={"executor": "reviewer"}))
        h_topic = metadata_hash(base.model_copy(update={"topic": "t"}))
        h_version = metadata_hash(base.model_copy(update={"current_plan_version": 2}))
        h_projection = metadata_hash(base.model_copy(update={"projection_id": uuid.uuid4()}))
        h_updated = metadata_hash(
            base.model_copy(update={"updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
        )
        # updated_at 不在排除集（与 created_at 同源 model_dump include）
        # —— 当前 metadata_hash 仅 exclude created_at / dir_path；
        # updated_at 变化应当影响 hash。
        assert h_executor != h_base
        assert h_topic != h_base
        assert h_version != h_base
        assert h_projection != h_base
        assert h_updated != h_base
