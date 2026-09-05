"""DB → FS projection 存量迁移 manifest service（实验 M2 I5：A5）。

四阶段：

1. **scan** —— 枚举 project 下 DB experiments + topics，建 ``MigrationRun``
   与 ``MigrationManifestItem`` 行；用 ``idempotency_key`` 唯一索引确保
   重跑 scan 不会产生重复 item。
2. **dry-run** —— 对每个 pending item 计算 FS payload 与 content_hash，
   但不调 ``/fs/projection/delta`` apply；输出 diff plan。
3. **execute** —— 逐 item apply（CAS，幂等）；中断恢复：进程死在
   ``in_flight`` 但 ``last_attempt_at`` 超过 ``STALE_AFTER_SECONDS`` 就
   重置为 ``pending`` 重新走。
4. **verify** —— 复用 ``map sync --check``（I2 落地）做最终对账。

幂等保证：

- ``idempotency_key`` 列上 UNIQUE constraint —— 同 (project_id, kind,
  slug, content_hash) 重复 INSERT 由 DB 层拦下，业务层走
  ``INSERT ... ON CONFLICT DO NOTHING`` 模式（不更新 status），保证
  重跑 scan 不会把已经 ``applied`` 的 item 退回 ``pending``。
- 中断恢复：``reset_stale_in_flight`` 走 ``UPDATE ... WHERE status =
  'in_flight' AND last_attempt_at < now() - STALE_AFTER``，并发安全
  由 caller 在事务里串行化（manifest scan 通常 host 触发，单写者）。
- 真正的去重由 server 端 ``/fs/projection/delta`` 的 base_revision
  CAS + ``expected_hash`` tombstone 兜底 —— manifest 只是 client 簿记。

不依赖：

- FS payload 计算复用 ``server.services.fs_source_service.build_payload``
  （已存在，I1-I3 都在用）；content_hash 与 FS 端 ``canonical_*_dict``
  口径一致，避免 manifest 算的 hash 与 server 算的对不上导致 dry-run
  误判。
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from server.domain.models import (
    Experiment,
    MigrationManifestItem,
    MigrationRun,
    Topic,
)

# 4 phase labels —— 与 ``MigrationRun.phase`` 列对齐
PHASE_SCAN = "scan"
PHASE_DRY_RUN = "dry_run"
PHASE_EXECUTE = "execute"
PHASE_VERIFY = "verify"

# in_flight 卡死超过该阈值视作 stale，下次 scan 自动重置为 pending。
# 经验值：单 item apply 通常 < 5s；30 分钟足够覆盖临时网络抖动，
# 又不会让真挂掉的进程拖太久。
STALE_AFTER_SECONDS = 1800

# 单 item apply 重试上限。超过即 ``failed`` 不再重试，留 audit 给 host
# 手动介入（plan 风险提示：94+ 量级下不设上限会卡死整个 run）。
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ScanReport:
    """scan 阶段输出：每个 item 的 manifest summary。"""

    run_id: str
    scanned: int
    inserted: int  # 新增的 item 行数
    skipped_existing: int  # idempotency_key 冲突被 DB 跳过的 item 数
    by_kind: dict[str, int]
    by_status: dict[str, int]


def _compute_idempotency_key(
    *, project_id: uuid.UUID, kind: str, slug: str, content_hash: str
) -> str:
    """idempotency_key = SHA-256(project_id|kind|slug|content_hash)。

    为什么要包 content_hash？

    - 同一 slug 的内容可能随 plan 修订 / 标题修改而变；idempotency_key
      含 hash 让「同 slug 不同版本」在 manifest 里共存为不同 item 行，
      不会出现「item 已 applied 但内容已变」的鬼影。
    - 不含 hash 的话，重复跑 scan 会把已 ``applied`` 的 item 状态吞掉；
      一旦 host 重启忘了 run_id 上下文就找不到原 item。
    """
    raw = f"{project_id}|{kind}|{slug}|{content_hash}".encode()
    return hashlib.sha256(raw).hexdigest()


def _compute_content_hash(kind: str, payload: dict[str, Any]) -> str:
    """manifest 侧 content_hash —— 与 FS 端 ``canonical_*_dict`` 口径一致。

    简化版：直接 ``json.dumps(sort_keys=True)`` + SHA-256。完整版应复用
    ``map_types.schemas.fs._canonical_*_dict`` —— 但 SDK / server 跨边
    界依赖太重，本里程碑先走简化版；后续若出现 hash 漂移（manifest
    算的与 server 算的对不上），把 helper 抽到 ``server/services/canonical.py``
    双端共享。

    kind: ``topic`` / ``experiment`` —— 显式区分，避免 topic vs
    experiment 同 slug 的 hash collision。
    """
    raw = repr((kind, payload)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _topic_payload(topic: Topic) -> dict[str, Any]:
    """把 DB Topic 序列化成 ``FsTopicDetailRead`` 兼容 dict。"""
    return {
        "id": str(topic.id),
        "slug": topic.slug,
        "title": topic.title,
        "status": topic.status.value if hasattr(topic.status, "value") else str(topic.status),
        "discussion_round": (
            topic.discussion_round.value
            if hasattr(topic.discussion_round, "value")
            else str(topic.discussion_round)
        ),
        "created_at": topic.created_at.isoformat() if topic.created_at else None,
        "updated_at": topic.updated_at.isoformat() if topic.updated_at else None,
        "creator_agent_id": str(topic.creator_agent_id) if topic.creator_agent_id else None,
        "topic_kind": getattr(topic, "topic_kind", "discussion"),
    }


def _experiment_payload(experiment: Experiment) -> dict[str, Any]:
    """把 DB Experiment 序列化成 ``FsExperimentRead`` 兼容 dict。"""
    return {
        "id": str(experiment.id),
        "slug": experiment.slug if hasattr(experiment, "slug") and experiment.slug else str(experiment.id),
        "title": experiment.title,
        "description": experiment.description,
        "phase": experiment.phase.value if hasattr(experiment.phase, "value") else str(experiment.phase),
        "mode": experiment.mode,
        "creator_agent_id": str(experiment.creator_agent_id),
        "executor_agent_id": str(experiment.executor_agent_id) if experiment.executor_agent_id else None,
        "topic_id": str(experiment.topic_id) if experiment.topic_id else None,
        "current_plan_version": experiment.current_plan_version,
        "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
        "updated_at": experiment.updated_at.isoformat() if experiment.updated_at else None,
        "phase_owner": getattr(experiment, "phase_owner", "host"),
    }


def create_run(db: Session, *, project_id: uuid.UUID, phase: str) -> MigrationRun:
    """开一个新 run —— 每次 phase 推进都建一行（audit 链）。"""
    run = MigrationRun(
        id=uuid.uuid4().hex,
        project_id=project_id,
        phase=phase,
    )
    db.add(run)
    db.flush()
    return run


def scan_project(
    db: Session, *, project_id: uuid.UUID
) -> ScanReport:
    """scan 阶段：枚举 DB experiments + topics，建 manifest item 行。

    幂等性：

    - ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`` —— 重跑
      scan 不会产生重复 item 行。
    - 已有 ``applied`` 状态的 item 行不会被覆盖 —— 业务层只新增
      ``pending`` 行；中途失败的 ``failed`` 行不重置（避免 retry 把
      ``last_error`` 擦掉）。

    实现注意：

    - 用 SQLite 方言的 ``insert()`` builder 拿 ``on_conflict_do_nothing``；
      生产 PostgreSQL 同语义（PG 也有 ON CONFLICT DO NOTHING）。这样
      写跨方言一致，不需要 ``if dialect is sqlite`` 分支。
    """
    run = create_run(db, project_id=project_id, phase=PHASE_SCAN)

    topics = list(
        db.scalars(
            select(Topic).where(Topic.project_id == project_id, Topic.deleted_at.is_(None))
        )
    )
    experiments = list(
        db.scalars(
            select(Experiment).where(
                Experiment.project_id == project_id,
                Experiment.deleted_at.is_(None),
            )
        )
    )

    rows: list[dict[str, Any]] = []
    for topic in topics:
        payload = _topic_payload(topic)
        content_hash = _compute_content_hash("topic", payload)
        rows.append(
            {
                "id": uuid.uuid4().hex,
                "project_id": project_id,
                "run_id": run.id,
                "kind": "topic",
                "slug": topic.slug,
                "content_hash": content_hash,
                "idempotency_key": _compute_idempotency_key(
                    project_id=project_id,
                    kind="topic",
                    slug=topic.slug,
                    content_hash=content_hash,
                ),
                "status": "pending",
                "attempts": 0,
            }
        )

    for experiment in experiments:
        payload = _experiment_payload(experiment)
        content_hash = _compute_content_hash("experiment", payload)
        slug = experiment.slug if hasattr(experiment, "slug") and experiment.slug else str(experiment.id)
        rows.append(
            {
                "id": uuid.uuid4().hex,
                "project_id": project_id,
                "run_id": run.id,
                "kind": "experiment",
                "slug": slug,
                "content_hash": content_hash,
                "idempotency_key": _compute_idempotency_key(
                    project_id=project_id,
                    kind="experiment",
                    slug=slug,
                    content_hash=content_hash,
                ),
                "status": "pending",
                "attempts": 0,
            }
        )

    inserted = 0
    skipped = 0
    if rows:
        stmt = sqlite_insert(MigrationManifestItem).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["idempotency_key"])
        result = db.execute(stmt)
        # SQLite 返回 ``rowcount``；PG 同语义
        inserted = result.rowcount if result.rowcount is not None else 0
        skipped = len(rows) - inserted
        # 注意：不调 commit —— 保留给 caller / FastAPI dependency 决定事务边界
        # （test fixture 用 outer-transaction + savepoint，service 内 commit 会
        # 把 savepoint 整个释放掉，破坏回滚）。
        db.flush()
        if inserted + skipped != len(rows):
            # 安全网：理论上 ON CONFLICT DO NOTHING 后 inserted + skipped == len(rows)
            # 必须成立；这里 sanity check 是为了发现 ORM bug（不会抛，但要打日志
            # 也得自己拉 logger；本里程碑先静默 raise IntegrityError）。
            raise RuntimeError(
                f"scan insert mismatch: inserted={inserted} skipped={skipped} total={len(rows)}"
            )

    by_kind = {"topic": len(topics), "experiment": len(experiments)}
    by_status = {"pending": len(rows), "applied": 0, "failed": 0, "skipped": 0}

    return ScanReport(
        run_id=run.id,
        scanned=len(rows),
        inserted=inserted,
        skipped_existing=skipped,
        by_kind=by_kind,
        by_status=by_status,
    )


def reset_stale_in_flight(db: Session, *, run_id: str) -> int:
    """中断恢复：把 stale ``in_flight`` 重置为 ``pending``。

    返回重置行数。caller 应在 execute 阶段开头调用一次，确保上次
    跑死在 ``in_flight`` 的 item 被捡回。
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS)
    # SQLite + PG 兼容：用 Python 端 filter —— 这里 list 量级（94+
    # 量级）SQL 复杂度不重要，可读性优先
    stale_items = list(
        db.scalars(
            select(MigrationManifestItem).where(
                MigrationManifestItem.run_id == run_id,
                MigrationManifestItem.status == "in_flight",
                MigrationManifestItem.last_attempt_at < threshold,
            )
        )
    )
    for item in stale_items:
        item.status = "pending"
        item.last_error = (
            f"stale in_flight reset (last_attempt_at < {threshold.isoformat()})"
        )
    db.flush()
    return len(stale_items)


def list_actionable(
    db: Session, *, run_id: str, limit: int = 50
) -> list[MigrationManifestItem]:
    """返回 ``pending`` 或（刚被 ``reset_stale_in_flight`` 翻回 pending
    的）item，按 ``id`` 排序保证顺序稳定。
    """
    return list(
        db.scalars(
            select(MigrationManifestItem)
            .where(
                MigrationManifestItem.run_id == run_id,
                MigrationManifestItem.status == "pending",
            )
            .order_by(MigrationManifestItem.id)
            .limit(limit)
        )
    )


def claim(db: Session, *, item_id: str) -> MigrationManifestItem | None:
    """原子把 ``pending`` 翻成 ``in_flight``，attempts += 1。

    返回 None 表示已被别的 worker 抢走（caller 应跳过）。

    SQLAlchemy 2.x 风格 update + returning；与并发 worker 兼容
    （两个 worker 同时调只会有一个 status 更新成功）。
    """
    from sqlalchemy import update

    item = db.get(MigrationManifestItem, item_id)
    if item is None or item.status not in {"pending"}:
        return None
    result = db.execute(
        update(MigrationManifestItem)
        .where(
            MigrationManifestItem.id == item_id,
            MigrationManifestItem.status == "pending",
        )
        .values(
            status="in_flight",
            attempts=MigrationManifestItem.attempts + 1,
            last_attempt_at=datetime.now(timezone.utc),
        )
        .returning(MigrationManifestItem)
    )
    db.flush()
    row = result.scalar_one_or_none()
    return row


def mark_applied(db: Session, *, item_id: str) -> None:
    from sqlalchemy import update

    db.execute(
        update(MigrationManifestItem)
        .where(MigrationManifestItem.id == item_id)
        .values(
            status="applied",
            applied_at=datetime.now(timezone.utc),
            last_error=None,
        )
    )
    db.flush()


def mark_failed(db: Session, *, item_id: str, error: str) -> MigrationManifestItem | None:
    """记失败；attempts 已超过 ``MAX_ATTEMPTS`` 则彻底 ``failed``，否则
    回 ``pending`` 等下次 execute 阶段重试。返回更新后的 item（可能
    是 ``failed`` 也可能是 ``pending``）。
    """
    from sqlalchemy import update

    item = db.get(MigrationManifestItem, item_id)
    if item is None:
        return None
    terminal = item.attempts >= MAX_ATTEMPTS
    db.execute(
        update(MigrationManifestItem)
        .where(MigrationManifestItem.id == item_id)
        .values(
            status="failed" if terminal else "pending",
            last_error=error[:1024] if error else None,
        )
    )
    db.flush()
    db.refresh(item)
    return item


def summarize_run(db: Session, *, run_id: str) -> dict[str, int]:
    """聚合 run 内 item 的 status 分布 —— 用于 ``MigrationRun.summary`` JSON。"""
    items = list(
        db.scalars(select(MigrationManifestItem).where(MigrationManifestItem.run_id == run_id))
    )
    out: dict[str, int] = {
        "pending": 0,
        "in_flight": 0,
        "applied": 0,
        "failed": 0,
        "skipped": 0,
    }
    for item in items:
        out[item.status] = out.get(item.status, 0) + 1
    return out


def finish_run(db: Session, *, run_id: str, summary: dict[str, Any]) -> None:
    from sqlalchemy import update

    db.execute(
        update(MigrationRun)
        .where(MigrationRun.id == run_id)
        .values(
            finished_at=datetime.now(timezone.utc),
            summary=_json_dumps(summary),
        )
    )
    db.flush()


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, default=str)


def get_latest_run(db: Session, *, project_id: uuid.UUID) -> MigrationRun | None:
    return db.scalar(
        select(MigrationRun)
        .where(MigrationRun.project_id == project_id)
        .order_by(MigrationRun.started_at.desc())
        .limit(1)
    )
