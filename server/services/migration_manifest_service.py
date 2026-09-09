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
    """manifest 侧 content_hash —— canonical JSON 口径（实验 M3 A3 收口）。

    历史（M2 I5）版本是 ``repr((kind, payload))``：依赖 dict 插入序、与
    FS 端 canonical JSON 口径必然不一致，导致 manifest hash 无法与任何
    server / FS 侧 hash 互认。现改为 canonical JSON（sort_keys +
    紧凑分隔符 + UTF-8），与 ``sdk/python/map_types/schemas/fs.py`` 的
    ``_sha256_canonical`` 同构；manifest / server / CLI 三方对同一
    payload 恒定同 hash。口径变更使旧 hash 全量失效 —— 重跑 ``scan``
    会以新 hash 重建 item（旧行留 ``applied`` 终态不影响，新行接手）。

    kind: ``topic`` / ``experiment`` —— 显式区分，避免 topic vs
    experiment 同 slug 的 hash collision。
    """
    raw = _canonical_json({"kind": kind, "payload": payload}).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(obj: Any) -> str:
    import json

    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


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


def reset_stale_in_flight(db: Session, *, project_id: uuid.UUID) -> int:
    """中断恢复：把 stale ``in_flight`` 重置为 ``pending``（实验 M3 A2 收口）。

    历史（M2 I5）版本按 ``run_id`` 过滤，而 CLI 传的是「本次刚建的 run」
    —— 新 run 的 item 全是 pending，恒 no-op；上次中断卡死在旧 run 的
    ``in_flight`` 行永远不会被捡回。现按 ``project_id`` 全局过滤，语义
    与「project 还有多少卡死项」一致。

    返回重置行数。caller 应在 execute 阶段开头调用一次。
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS)
    # SQLite + PG 兼容：用 Python 端 filter —— 这里 list 量级（94+
    # 量级）SQL 复杂度不重要，可读性优先
    stale_items = list(
        db.scalars(
            select(MigrationManifestItem).where(
                MigrationManifestItem.project_id == project_id,
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


def list_project_actionable(
    db: Session, *, project_id: uuid.UUID, limit: int = 50
) -> list[MigrationManifestItem]:
    """全 project 范围待处理项（``pending`` + ``skipped``）。

    CLI dry-run / execute 阶段用：跨 run 取所有未完成 item（含旧 run 漏掉
    的、或 retry budget 未耗尽的），让 host 一次能看到「project 还有多少
    没跑完」，而不是「最新一次 scan 又扫到几个」（重跑 scan 通常 idempotent
    跳过，新 run 没新 item）。

    ``skipped`` 是 dry execute 的排练标记（实验 M3 A2：dry 路径不再伪造
    ``applied`` 终态）——它仍未真正写 server，``--apply`` 一轮即可捡回。
    """
    return list(
        db.scalars(
            select(MigrationManifestItem)
            .where(
                MigrationManifestItem.project_id == project_id,
                MigrationManifestItem.status.in_(("pending", "skipped")),
            )
            .order_by(MigrationManifestItem.id)
            .limit(limit)
        )
    )


def claim(db: Session, *, item_id: str) -> MigrationManifestItem | None:
    """原子把 ``pending``（或 ``skipped``）翻成 ``in_flight``，attempts += 1。

    ``skipped`` 是 dry execute 的排练标记（实验 M3 A2）——它没有被真
    写过 server，``--apply`` 一轮必须能捡回，否则 skipped 成为不可逾越
    的死态（live 收口实测踩中：actionable 列出了 7 个 skipped，
    claim 全拒 → processed=0）。

    返回 None 表示已被别的 worker 抢走（caller 应跳过）。

    SQLAlchemy 2.x 风格 update + returning；与并发 worker 兼容
    （两个 worker 同时调只会有一个 status 更新成功）。
    """
    from sqlalchemy import update

    item = db.get(MigrationManifestItem, item_id)
    if item is None or item.status not in {"pending", "skipped"}:
        return None
    result = db.execute(
        update(MigrationManifestItem)
        .where(
            MigrationManifestItem.id == item_id,
            MigrationManifestItem.status.in_(("pending", "skipped")),
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


# ---------------------------------------------------------------------------
# stale 语义六类（实验 M2 I6：A6）
# ---------------------------------------------------------------------------
#
# execute 阶段每个 item apply 失败时，按失败原因归类到下面 6 类 stale code。
# 把分类抽到集中表里，理由：
#
# 1. host 运维 / reviewer 看 ``last_error`` 字段时需要稳定 code 才能 grep
#    / 写 alert 规则（"STALE_PAYLOAD_TOO_LARGE 触发就拒绝并通知"）。
# 2. 不同 code 对应不同修复路径（CAS 重试 vs 减 payload vs 改 publisher）；
#    分类驱动下一步动作。
# 3. 留扩展位：未来 server 端新增错误类型时，加一个新 code 即可，不需要
#    改 ``mark_failed`` / CLI 输出 / 文档。
#
# 实现：``classify_stale_code(exc)`` 走启发式（HTTP status + detail 关键词），
# 不依赖 server 端在 detail 里携带 ``code`` 字段（目前 server 错误格式不统一）。
# 启发式失败兜底为 ``STALE_OTHER``。

STALE_PROJECTION_REVISION_CONFLICT = "stale.projection_revision_conflict"
STALE_CONTENT_HASH_MISMATCH = "stale.content_hash_mismatch"
STALE_PUBLISHER_NOT_ALLOWED = "stale.publisher_not_allowed"
STALE_PAYLOAD_TOO_LARGE = "stale.payload_too_large"
STALE_WRITE_TOKEN_REPLAY = "stale.write_token_replay"
STALE_SCHEMA_MISMATCH = "stale.schema_mismatch"
STALE_OTHER = "stale.other"

STALE_ALL_CODES: tuple[str, ...] = (
    STALE_PROJECTION_REVISION_CONFLICT,
    STALE_CONTENT_HASH_MISMATCH,
    STALE_PUBLISHER_NOT_ALLOWED,
    STALE_PAYLOAD_TOO_LARGE,
    STALE_WRITE_TOKEN_REPLAY,
    STALE_SCHEMA_MISMATCH,
    STALE_OTHER,
)


def _exc_text(exc: BaseException | dict | str) -> str:
    """从异常 / dict / str 提取可分类的文本。"""
    if isinstance(exc, BaseException):
        return f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, dict):
        # MAP API 错误通常 ``{"detail": "..."}`` 或 ``{"code": "...", "detail": "..."}``
        parts = []
        for k in ("code", "detail", "message", "error"):
            v = exc.get(k)
            if v:
                parts.append(str(v))
        return " ".join(parts).lower()
    return str(exc).lower()


def classify_stale_code(exc: BaseException | dict | str) -> str:
    """把 server 端异常 / API error body 归到 6 类 stale code 之一。

    分类规则（按优先级匹配；先命中先用）：

    1. ``projection revision conflict`` / ``base_revision`` → CAS
       revision 漂移（C3 触发频率最高，CAS retry 兜底）
    2. ``content hash mismatch`` / ``expected_hash`` → tombstone 拒绝
       （写端期望的 hash 与 server 端 projection 现存 hash 不一致）
    3. ``publisher not allowed`` / ``publisher_agent_id`` → 权限拒绝
       （走的是非 host/admin publisher）
    4. ``too large`` / ``payload size`` / ``exceeds`` / ``413`` → 容量超限
       （payload > ``_PROJECTION_MAX_BYTES`` 或单 object > ``_PROJECTION_MAX_OBJECT_BYTES``）
    5. ``token replay`` / ``write_token`` / ``nonce`` → 写 token 重放
       （fs_write_receipts 唯一索引命中）
    6. ``validation`` / ``schema`` / ``422`` / ``unprocessable`` → schema 失败
       （Pydantic validation）
    7. 其他 → ``STALE_OTHER``（兜底；后续分类细化时再加 code）
    """
    text = _exc_text(exc)

    # 1. CAS revision
    if any(
        kw in text
        for kw in (
            "projection revision conflict",
            "base_revision",
            "expected revision",
            "cas conflict",
        )
    ):
        return STALE_PROJECTION_REVISION_CONFLICT

    # 2. content hash mismatch (tombstone)
    if any(
        kw in text
        for kw in (
            "content hash mismatch",
            "expected_hash",
            "hash mismatch",
            "tombstone",
        )
    ):
        return STALE_CONTENT_HASH_MISMATCH

    # 3. publisher not allowed
    if any(
        kw in text
        for kw in (
            "publisher not allowed",
            "publisher_agent_id",
            "not in publisher allowlist",
            "forbidden publisher",
        )
    ):
        return STALE_PUBLISHER_NOT_ALLOWED

    # 4. payload too large
    if any(
        kw in text
        for kw in (
            "payload too large",
            "payload size",
            "exceeds maximum",
            " 413 ",
            "max_bytes",
            "max_topics",
        )
    ):
        return STALE_PAYLOAD_TOO_LARGE

    # 5. write token replay
    if any(
        kw in text
        for kw in (
            "write token replay",
            "token replay",
            "nonce already used",
            "write_token",
            "fs_write_receipts",
        )
    ):
        return STALE_WRITE_TOKEN_REPLAY

    # 6. schema mismatch
    if any(
        kw in text
        for kw in (
            "validation",
            "schema mismatch",
            " 422 ",
            "unprocessable",
            "validation error",
            "field required",
        )
    ):
        return STALE_SCHEMA_MISMATCH

    return STALE_OTHER


def mark_failed_with_stale(
    db: Session, *, item_id: str, error: str, exc: BaseException | dict | str
) -> MigrationManifestItem | None:
    """``mark_failed`` 的 stale-aware 变体：把分类 code 拼到 ``last_error`` 前缀。

    last_error 格式：``[<stale_code>] <原 error 文本>``。host / reviewer 工具
    按 ``]`` 前缀 grep 即可拿到 stable code，不需要重新跑分类。
    """
    code = classify_stale_code(exc)
    tagged = f"[{code}] {error}" if error else f"[{code}]"
    return mark_failed(db, item_id=item_id, error=tagged)


# ---------------------------------------------------------------------------
# last-known-good fallback（实验 M2 I6：A6）
# ---------------------------------------------------------------------------
#
# 背景：execute 阶段任何时刻都可能因为 publisher 不在 / projection 被
# 别的 agent 推进 / 服务重启而拿不到 server 端当前 inventory。这时候
# 单看 manifest 与 server 不一致无法区分「server 错了」与「manifest 漏了」。
#
# LKG 设计：
#
# - 每次 ``finish_run`` 成功落盘后，把 applied item 的 (kind, slug,
#   content_hash) 列表写到 ``<content_root>/.fs-migration/last-known-good.json``
# - 下次 scan 之前 CLI 先 read LKG：存在 → 把 LKG 项与新 scan 项 diff；
#   不存在 → 不阻断，只警告「首次迁移，无 LKG anchor」
# - LKG 是 **client-side** anchor（不写 server），不参与 server CAS；
#   它只用于「DB 那边有，server 那边没了，应该相信谁」的判断提示
#
# 不依赖：完全不参与 server CAS，纯本地 anchor；服务端无感。
#
# M3 A2 起的实际落点：verify 收进 server API（按 ``projection_id`` 逐字段
# 比对，见 ``verify_project``），CLI 侧 LKG 锚点改为 project 作用域的
# ``<content_root>/.fs-migration/<project_key>-lkg.json``（v2 schema，
# 原子写，见 ``cli/commands/migration_manifest.py``）。本节 helpers 保留
# 供 ``tests/test_stale_and_lkg.py`` 钉住的 bucket 语义（in_both /
# in_db_only / in_lkg_only / hash_drift）复用；生产路径已不再调用。

LKG_RELATIVE_PATH = ".fs-migration/last-known-good.json"


def build_lkg_payload(
    *, project_id: uuid.UUID, run_id: str, items: list[MigrationManifestItem]
) -> dict[str, Any]:
    """构造 LKG 文件 payload —— 调用方写盘。"""
    return {
        "schema": "fs-migration.last-known-good/v1",
        "project_id": str(project_id),
        "run_id": run_id,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "items": [
            {
                "kind": item.kind,
                "slug": item.slug,
                "content_hash": item.content_hash,
                "idempotency_key": item.idempotency_key,
                "applied_at": item.applied_at.isoformat() if item.applied_at else None,
            }
            for item in items
            if item.status == "applied"
        ],
    }


def diff_against_lkg(
    *, db_items: list[MigrationManifestItem], lkg: dict[str, Any] | None
) -> dict[str, Any]:
    """新 scan 项与 LKG 项 diff —— 用于 ``verify`` 阶段报告。

    返回结构::

        {
            "lkg_present": bool,
            "lkg_run_id": str | None,
            "in_lkg_only": [...],   # server 上一次 apply 过，本地 DB 已经删除/改 hash
            "in_db_only": [...],    # 本地 DB 有新内容，server 没跟上
            "in_both": [...],       # 双侧都有且 hash 一致（健康）
            "hash_drift": [...],    # 双侧都有但 hash 不一致（server 与 DB 内容漂移）
        }
    """
    db_by_key: dict[str, dict[str, Any]] = {}
    for item in db_items:
        # 用 (kind, slug, content_hash) 元组作 key —— hash 一致才算健康
        db_by_key[f"{item.kind}|{item.slug}|{item.content_hash}"] = {
            "kind": item.kind,
            "slug": item.slug,
            "content_hash": item.content_hash,
        }

    lkg_items = (lkg or {}).get("items") or []
    lkg_by_key: dict[str, dict[str, Any]] = {}
    lkg_by_slug: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in lkg_items:
        key = f"{entry['kind']}|{entry['slug']}|{entry['content_hash']}"
        lkg_by_key[key] = entry
        slug_key = (entry["kind"], entry["slug"])
        lkg_by_slug.setdefault(slug_key, []).append(entry)

    in_both: list[dict[str, Any]] = []
    in_db_only: list[dict[str, Any]] = []
    hash_drift: list[dict[str, Any]] = []

    for key, entry in db_by_key.items():
        if key in lkg_by_key:
            in_both.append(entry)
        else:
            # 看 LKG 里同 (kind, slug) 是否还有别的 hash → hash drift
            same_slug = lkg_by_slug.get((entry["kind"], entry["slug"]), [])
            if same_slug:
                hash_drift.append(
                    {
                        **entry,
                        "lkg_content_hashes": [s["content_hash"] for s in same_slug],
                    }
                )
            else:
                in_db_only.append(entry)

    in_lkg_only: list[dict[str, Any]] = []
    for key, entry in lkg_by_key.items():
        if key not in db_by_key:
            in_lkg_only.append(entry)

    return {
        "lkg_present": lkg is not None,
        "lkg_run_id": (lkg or {}).get("run_id"),
        "lkg_written_at": (lkg or {}).get("written_at"),
        "in_lkg_only": in_lkg_only,
        "in_db_only": in_db_only,
        "in_both": in_both,
        "hash_drift": hash_drift,
        "summary": {
            "in_lkg_only": len(in_lkg_only),
            "in_db_only": len(in_db_only),
            "in_both": len(in_both),
            "hash_drift": len(hash_drift),
        },
    }


def summarize_run(db: Session, *, run_id: str) -> dict[str, int]:
    """聚合 run 内 item 的 status 分布 —— 用于 ``MigrationRun.summary`` JSON。"""
    items = list(
        db.scalars(select(MigrationManifestItem).where(MigrationManifestItem.run_id == run_id))
    )
    return _count_by_status(items)


def summarize_project(db: Session, *, project_id: uuid.UUID) -> dict[str, int]:
    """跨 run 全 project 聚合 —— 用于 CLI ``execute --json`` 报告。

    跨 run execute（idempotent scan + cross-run claim）会让本 run 0 item，
    但 status 实际变化是 project 范围的；用本函数得到真值。
    """
    items = list(
        db.scalars(
            select(MigrationManifestItem).where(
                MigrationManifestItem.project_id == project_id
            )
        )
    )
    return _count_by_status(items)


def _count_by_status(items: list[MigrationManifestItem]) -> dict[str, int]:
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
        .order_by(MigrationRun.started_at.desc(), MigrationRun.id.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------------
# server 端 execute / plan / verify（实验 M3 A2 收口）
# ---------------------------------------------------------------------------
#
# 历史（M2 I5/I6）的 execute 循环活在 CLI 进程里：直连 server DB（绕过
# API 层，无权限/审计）、整循环单事务（进程死 → 全回滚，「断点」不存在）、
# dry 路径伪造 applied 终态、--apply=true 是无条件 raise。现整体搬进
# service，由 API 层调用（CLI 走 HTTP）：
#
# - 逐 item **commit**：进程中断时已完成 item 保持 applied，未完成保持
#   pending —— 断点真实存在，重跑 execute 从断点继续。
# - ``apply=True`` 走 ``fs_projection_store.apply_fs_projection_delta``
#   真 CAS（base_revision 冲突自动刷新重试）；apply 要求 DB 实体在
#   server FS 视角已有对应物（experiments 按 ``projection_id`` 关联、
#   topics 按 slug），无对应物 fail closed 留指引 —— 不造幽灵身份。
# - dry（``apply=False``）记 ``skipped``（可被 --apply 捡回），不再伪造
#   ``applied``。


def plan_project(
    db: Session, *, project_id: uuid.UUID, limit: int = 50
) -> dict[str, Any]:
    """只读 plan（dry-run 用）：零 DB 写副作用。

    stale ``in_flight`` 只**计数**不重置——重置是 execute 的职责；dry-run
    保持纯读（CLI ``dry-run`` 未登记写命令，host worker --dry-run 模式
    会真实执行它，不能有副作用）。
    """
    pending = list_project_actionable(db, project_id=project_id, limit=limit)
    threshold = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS)
    stale_count = len(
        list(
            db.scalars(
                select(MigrationManifestItem).where(
                    MigrationManifestItem.project_id == project_id,
                    MigrationManifestItem.status == "in_flight",
                    MigrationManifestItem.last_attempt_at < threshold,
                )
            )
        )
    )
    return {
        "actionable_count": len(pending),
        "stale_in_flight": stale_count,
        "summary": summarize_project(db, project_id=project_id),
        "actionable": [
            {
                "id": it.id,
                "kind": it.kind,
                "slug": it.slug,
                "content_hash": it.content_hash,
                "attempts": it.attempts,
                "status": it.status,
            }
            for it in pending
        ],
    }


def _mark_skipped(db: Session, *, item_id: str) -> None:
    from sqlalchemy import update

    db.execute(
        update(MigrationManifestItem)
        .where(MigrationManifestItem.id == item_id)
        .values(status="skipped", last_error="dry execute (no server write)")
    )
    db.flush()


def _experiment_view_counterpart(view: list[Any], entity_id: uuid.UUID):
    """DB 实验 ↔ server FS 视角关联：index.md frontmatter ``projection_id``。"""
    for item in view:
        if item.projection_id is not None and uuid.UUID(str(item.projection_id)) == entity_id:
            return item
    return None


def _agent_name(db: Session, agent_id: uuid.UUID | None) -> str:
    """DB agent 全名 → FS 口径（persona 短名）。

    ``index.md`` 的 creator/executor 存 persona 短名（host/participant/
    reviewer，见 ``fs_source_service.persona_short_name`` 约定）；DB 存
    全名（multi-agent-platform-host）。比对与写值必须走同一归一化，
    否则全量实验恒报 creator/executor mismatch（live 收口实证）。
    自定义 agent（无 canonical 尾缀）保持全名参与比对。
    """
    if agent_id is None:
        return ""
    from map_types.persona import persona_from_agent_name

    from server.domain.models import Agent

    agent = db.get(Agent, agent_id)
    if agent is None:
        return ""
    return persona_from_agent_name(agent.name) or agent.name


def _merged_experiment_value(db: Session, entity, counterpart) -> Any:
    """以 FS 对应物为底、DB 权威字段打补丁，构造 delta upsert value。

    DB 是生命周期权威（phase / title / description / plan version）；
    身份字段（id / slug / dir_path）保持对应物原值，避免造幽灵身份。
    """
    from map_types.enums import ExperimentPhase
    from map_types.schemas.fs import FsExperimentRead

    assert isinstance(counterpart, FsExperimentRead)
    phase = (
        entity.phase.value if hasattr(entity.phase, "value") else str(entity.phase)
    )
    ExperimentPhase(phase)  # 校验合法，非法值在打补丁前即失败
    return counterpart.model_copy(
        update={
            "title": entity.title,
            "description": entity.description or "",
            "phase": phase,
            "current_plan_version": entity.current_plan_version or 1,
            "updated_at": entity.updated_at,
            "creator": _agent_name(db, entity.creator_agent_id) or counterpart.creator,
            "executor": _agent_name(db, entity.executor_agent_id),
            "projection_id": entity.id,
        }
    )


def _push_delta(db: Session, *, project, agent, changes: list[Any], retries: int = 3):
    """单批 delta 的真 CAS 推送：base_revision 冲突自动刷新重试。"""
    from server.services.errors import ConflictError
    from server.services.fs_projection_store import (
        apply_fs_projection_delta,
        get_fs_projection,
    )

    last_exc: Exception | None = None
    for _attempt in range(retries):
        row = get_fs_projection(db, project)
        if row is None:
            raise RuntimeError(
                "FS projection 不存在；迁移 apply 的 delta CAS 需要投影主行。"
                "remote/容器模式：先 `map sync publish --full` 建投影再重试；"
                "local 模式：server 实时读 FS，无需 apply——直接 `map sync "
                "migrate verify` 对账 DB↔FS 字段对齐（live 收口实证）"
            )
        payload = _build_delta_request(db, project=project, row=row, changes=changes)
        try:
            return apply_fs_projection_delta(db, project, agent, payload)
        except ConflictError as exc:
            last_exc = exc
            text = str(exc).lower()
            if "revision" not in text and "conflict" not in text:
                raise
            db.rollback()
    raise last_exc  # pragma: no cover — retries>1 时必不触达


def _build_delta_request(db: Session, *, project, row, changes: list[Any]):
    """按当前投影快照预演 changes，构造带正确 ``result_content_hash`` 的请求。"""
    from map_types.schemas.fs import FsProjectionDeltaRequest

    from server.services.fs_projection_store import (
        fs_projection_content_hash,
        projection_payload_experiments,
        projection_payload_topics,
    )

    topics = {t.slug: t for t in projection_payload_topics(row)}
    experiments = {e.slug: e for e in projection_payload_experiments(row)}
    for change in changes:
        if change.kind == "topic_upsert":
            topics[change.slug] = change.value
        elif change.kind == "experiment_upsert":
            experiments[change.slug] = change.value
        else:  # pragma: no cover — 迁移 apply 只产 upsert
            raise RuntimeError(f"unsupported migration change kind: {change.kind}")
    result_hash = fs_projection_content_hash(list(topics.values()), list(experiments.values()))
    return FsProjectionDeltaRequest(
        base_revision=row.revision,
        client_workspace=str(project.workspace_path or "server-side-migration"),
        content_root=project.content_root or "map",
        changes=changes,
        result_content_hash=result_hash,
    )


def _apply_item(db: Session, *, project, agent, item: MigrationManifestItem) -> str:
    """对单个 claimed item 执行真 apply，返回描述性结果。"""
    from map_types.schemas.fs import FsProjectionChange

    from server.services.fs_source_service import fs_experiments_view

    if item.kind == "experiment":
        from server.domain.models import Experiment

        entity = db.get(Experiment, uuid.UUID(item.slug))
        if entity is None:
            raise RuntimeError(f"experiment {item.slug} 不存在（已删除？）；scan 重建后再试")
        view = fs_experiments_view(db, project)
        counterpart = _experiment_view_counterpart(view, entity.id)
        if counterpart is None:
            raise RuntimeError(
                "无 FS 对应物（server FS 视角无 projection_id 指向本实验的条目）；"
                "为避免幽灵身份，迁移不凭空造投影条目——先经 validated write "
                "流程物化 map/experiments/ 目录后重试"
            )
        value = _merged_experiment_value(db, entity, counterpart)
        result = _push_delta(
            db,
            project=project,
            agent=agent,
            changes=[
                FsProjectionChange(
                    kind="experiment_upsert", slug=counterpart.slug, value=value
                )
            ],
        )
        return f"experiment_upsert slug={counterpart.slug} revision={result.revision}"
    raise RuntimeError(f"unsupported manifest kind: {item.kind}")


def execute_pending(
    db: Session,
    *,
    project,
    agent,
    apply: bool,
    limit: int = 50,
) -> dict[str, Any]:
    """server 端 execute 循环：reset → 逐 item claim/apply/mark → **逐 item commit**。"""
    project_id = project.id
    reset = reset_stale_in_flight(db, project_id=project_id)
    db.commit()

    items = list_project_actionable(db, project_id=project_id, limit=limit)
    processed = 0
    failed = 0
    unclaimed = 0
    for it in items:
        claimed = claim(db, item_id=it.id)
        if claimed is None:
            unclaimed += 1
            continue
        try:
            if apply:
                _apply_item(db, project=project, agent=agent, item=claimed)
                mark_applied(db, item_id=claimed.id)
            else:
                _mark_skipped(db, item_id=claimed.id)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            # rollback 会把 claim 一起回滚（status 回 pending / attempts 不加）；
            # 重新 claim 一次再记失败，保证 attempts 审计与错误留痕落库。
            reclaimed = claim(db, item_id=it.id)
            if reclaimed is None:  # pragma: no cover — 单写者下不发生
                unclaimed += 1
                continue
            mark_failed_with_stale(
                db, item_id=reclaimed.id, error=str(exc)[:256], exc=exc
            )
            failed += 1
        db.commit()

    summary = summarize_project(db, project_id=project_id)
    run = create_run(db, project_id=project_id, phase=PHASE_EXECUTE)
    finish_run(db, run_id=run.id, summary=summary)
    db.commit()
    return {
        "run_id": run.id,
        "apply": apply,
        "stale_in_flight_reset": reset,
        "processed": processed,
        "failed": failed,
        "unclaimed": unclaimed,
        "summary": summary,
    }


def verify_project(db: Session, *, project) -> dict[str, Any]:
    """对账：全量 DB 实验（迁移目标集）vs server FS 视角（A2 固定字段契约）。

    历史 verify 只做 client 侧 manifest↔LKG 自比对（谁也没碰 server）。
    现以 ``fs_source_service.fs_experiments_view``（local-fs 实时解析 /
    远端投影 cache，同源 ``sync check``）为对照侧，按 DB↔FS 身份关联
    （``projection_id``）逐字段比对——DB 权威字段 vs server 视角实时值。

    对账对象是 **project 全量未删除实验**，而不是 manifest ``applied``
    项：local 模式下 server 实时读 FS、无投影主行，``execute --apply``
    的 delta CAS 写不适用（那是 remote 模式的写面），manifest 永远到
    不了 applied——按 applied 过滤会让 local 模式的对账恒空集。
    manifest 状态只进 summary 作簿记参考。
    """
    from server.services.fs_source_service import fs_experiments_view

    view = fs_experiments_view(db, project)
    view_by_pid: dict[str, Any] = {}
    for v in view:
        if v.projection_id is not None:
            view_by_pid[str(v.projection_id)] = v

    entities = list(
        db.scalars(
            select(Experiment).where(
                Experiment.project_id == project.id,
                Experiment.deleted_at.is_(None),
            )
        )
    )
    verified: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    for entity in entities:
        counterpart = view_by_pid.get(str(entity.id))
        if counterpart is None:
            entry = {
                "kind": "experiment",
                "slug": str(entity.id),
                "reason": "no FS counterpart (projection_id 未指向本实验)",
            }
            # 终态相位（done/cancelled）无 FS 对应物 = legacy 残留，
            # 与 sync check 对 terminal fs_only 的宽容同语义（非 blocking）；
            # 活跃相位缺对应物才是真 missing（blocking）。
            entity_phase = (
                entity.phase.value
                if hasattr(entity.phase, "value")
                else str(entity.phase)
            )
            if entity_phase in _TERMINAL_PHASES:
                entry["phase"] = entity_phase
                legacy.append(entry)
            else:
                missing.append(entry)
            continue
        diffs = _align_field_diffs(db, entity, counterpart)
        entry = {"kind": "experiment", "slug": counterpart.slug, "fields": diffs}
        if not diffs:
            verified.append(entry)
            continue
        entity_phase = (
            entity.phase.value if hasattr(entity.phase, "value") else str(entity.phase)
        )
        audit_only = all(d["field"] in _AUDIT_ONLY_FIELDS for d in diffs)
        # 终态相位 + 仅审计字段（creator/executor）漂移 → legacy（非 blocking）：
        # 内容读消费的是 title/phase/description/plan_version，终态实体的
        # 审计字段不再参与任何门禁；与 sync check 对 terminal 的宽容同语义。
        # 漂移本身仍逐字段列出，不吞。内容字段漂移无论相位恒 blocking。
        if entity_phase in _TERMINAL_PHASES and audit_only:
            entry["phase"] = entity_phase
            legacy.append(entry)
        else:
            mismatched.append(entry)

    return {
        "verified_count": len(verified),
        "mismatch_count": len(mismatched),
        "missing_count": len(missing),
        "legacy_count": len(legacy),
        "verified": verified,
        "mismatched": mismatched,
        "missing": missing,
        "legacy": legacy,
        "summary": summarize_project(db, project_id=project.id),
    }


_ALIGN_FIELDS: tuple[str, ...] = (
    "title",
    "phase",
    "description",
    "current_plan_version",
    "creator",
    "executor",
)

# 终态相位：无 FS 对应物按 legacy（非 blocking）处理，与 sync check 对
# terminal fs_only 的宽容一致
_TERMINAL_PHASES: frozenset[str] = frozenset({"done", "cancelled"})

# 审计元数据字段：终态实体上漂移降级 legacy（内容读不消费）
_AUDIT_ONLY_FIELDS: frozenset[str] = frozenset({"creator", "executor"})


def _align_field_diffs(db: Session, entity, counterpart) -> list[dict[str, Any]]:
    """DB 权威字段 vs server 视角字段；返回不一致清单（A2 契约的 DB↔FS 投影）。"""
    phase = entity.phase.value if hasattr(entity.phase, "value") else str(entity.phase)
    db_values = {
        "title": entity.title,
        "phase": phase,
        "description": entity.description or "",
        "current_plan_version": entity.current_plan_version or 1,
        "creator": _agent_name(db, entity.creator_agent_id),
        "executor": _agent_name(db, entity.executor_agent_id),
    }
    view_values = {
        "title": counterpart.title,
        "phase": counterpart.phase,
        "description": counterpart.description or "",
        "current_plan_version": counterpart.current_plan_version or 1,
        "creator": counterpart.creator or "",
        "executor": counterpart.executor or "",
    }
    diffs = []
    for field in _ALIGN_FIELDS:
        if db_values[field] != view_values[field]:
            diffs.append(
                {"field": field, "db": db_values[field], "fs_view": view_values[field]}
            )
    return diffs
