"""迁移 manifest —— last-known-good 回退与 run 汇总（实验 M2 I6：A6）。

自 ``migration_manifest_service.py`` 拆出（模块 800 行上限，T46）。本模块
只依赖 models / sqlalchemy，不反向依赖宿主；宿主 re-export 保持导入路径不变。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import MigrationManifestItem, MigrationRun

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


