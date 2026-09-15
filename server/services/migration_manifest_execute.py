"""迁移 manifest —— server 端 plan / execute / verify（实验 M3 A2 收口）。

自 ``migration_manifest_service.py`` 拆出（模块 800 行上限，T46）。
依赖宿主（``claim`` / ``mark_applied`` / ``create_run`` / 常量等）走函数内
lazy import 反向引用，避免与宿主底部的 re-export 形成导入环；同层的
``migration_manifest_stale`` / ``migration_manifest_lkg`` 无环，直接顶层导入。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Experiment, MigrationManifestItem, PlanVersion
from server.services.migration_manifest_lkg import finish_run, summarize_project
from server.services.migration_manifest_stale import mark_failed_with_stale

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
    from server.services import migration_manifest_service as _core
    pending = _core.list_project_actionable(db, project_id=project_id, limit=limit)
    threshold = datetime.now(timezone.utc) - timedelta(seconds=_core.STALE_AFTER_SECONDS)
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
    from server.services import migration_manifest_service as _core
    reset = _core.reset_stale_in_flight(db, project_id=project_id)
    db.commit()

    items = _core.list_project_actionable(db, project_id=project_id, limit=limit)
    processed = 0
    failed = 0
    unclaimed = 0
    for it in items:
        claimed = _core.claim(db, item_id=it.id)
        if claimed is None:
            unclaimed += 1
            continue
        try:
            if apply:
                _apply_item(db, project=project, agent=agent, item=claimed)
                _core.mark_applied(db, item_id=claimed.id)
            else:
                _mark_skipped(db, item_id=claimed.id)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            # rollback 会把 claim 一起回滚（status 回 pending / attempts 不加）；
            # 重新 claim 一次再记失败，保证 attempts 审计与错误留痕落库。
            reclaimed = _core.claim(db, item_id=it.id)
            if reclaimed is None:  # pragma: no cover — 单写者下不发生
                unclaimed += 1
                continue
            mark_failed_with_stale(
                db, item_id=reclaimed.id, error=str(exc)[:256], exc=exc
            )
            failed += 1
        db.commit()

    summary = summarize_project(db, project_id=project_id)
    run = _core.create_run(db, project_id=project_id, phase=_core.PHASE_EXECUTE)
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

    # A2-2（实验 plan-db-content-retirement）：plan 域对账——DB 当前版
    # PlanVersion.content_md ↔ FS plan.md 内容一致性。scan 已登记 kind=plan
    # item；本对账证明「FS plan.md 已就位且与 DB 权威正文一致」，是 A2-2
    # stub 化 content_md（destructive）与 A3-1 读路径切 FS 的安全前置。
    #
    # 相位门禁：flag ``plan_db_content_retired`` OFF 时 plan 域问题按
    # **信息**处理（plan_domain_blocking=False，不进 mismatch/missing 计数）
    # ——DB 仍是权威、读路径未切、materialize 未跑完，此时报红会破
    # 验收 A1「flag off 行为与现状一致」，也会误伤所有 FS 制品尚未物化
    # 的存量实验。flag ON 时 plan 域问题才升级为 blocking。
    #
    # 分类：
    #   verified — FS plan.md 与 DB content_md 字节一致；或 DB 已是 stub
    #              （``See file:`` / ``<!-- slim create:``）指向 FS 现存文件
    #              → 视为已就位（A2-2 stub 化 / slim create 完成态）。
    #   mismatch — 两侧都存在但字节不同；DB 权威正文需 materialize 覆盖 FS。
    #   missing  — 无 plan_file_path，或 plan_file_path 指向的文件不存在；
    #              需 `map experiment plan materialize`（creator 通道）先物化。
    from server.services.feature_flag_service import is_plan_db_content_retired_on

    plan_flag_on = is_plan_db_content_retired_on(db, project.id)
    plan_verified: list[dict[str, Any]] = []
    plan_mismatched: list[dict[str, Any]] = []
    plan_missing: list[dict[str, Any]] = []
    current_plan_by_exp: dict[uuid.UUID, PlanVersion] = {}
    for plan in current_plans_all(db, project_id=project.id):
        current_plan_by_exp[plan.experiment_id] = plan
    for entity in entities:
        plan = current_plan_by_exp.get(entity.id)
        if plan is None:
            continue  # 无当前版 PlanVersion → 不入 plan 域对账
        entry = _verify_plan_entry(db, project=project, entity=entity, plan=plan)
        bucket = entry.pop("bucket")
        if bucket == "verified":
            plan_verified.append(entry)
        elif bucket == "mismatch":
            plan_mismatched.append(entry)
        else:
            plan_missing.append(entry)

    # 顶层 mismatch/missing 计数保持 experiment 域语义不变（现有测试 + CLI
    # 逐条列举只覆盖 experiment 域）。plan 域的 blocking 信号在
    # plan_domain.blocking / plan_domain.{mismatch,missing}_count 单独暴露，
    # 由 caller（CLI verify / host 编排）在 flag ON 时一并检查——CLI 展示
    # 更新随 A3-2（文档与分发面）接线。
    return {
        "verified_count": len(verified),
        "mismatch_count": len(mismatched),
        "missing_count": len(missing),
        "legacy_count": len(legacy),
        "verified": verified,
        "mismatched": mismatched,
        "missing": missing,
        "legacy": legacy,
        # A2-2 plan 域对账（附加、独立计数；flag OFF 时不污染上面 blocking 计数）
        "plan_domain": {
            "flag_on": plan_flag_on,
            "blocking": plan_flag_on,
            "verified_count": len(plan_verified),
            "mismatch_count": len(plan_mismatched),
            "missing_count": len(plan_missing),
            "verified": plan_verified,
            "mismatched": plan_mismatched,
            "missing": plan_missing,
        },
        "summary": summarize_project(db, project_id=project.id),
    }


def current_plans_all(db: Session, *, project_id: uuid.UUID) -> list[PlanVersion]:
    """project 下所有未删除实验的**当前版** PlanVersion（对账侧使用）。

    与 ``scan_project`` 里的 current_plans 查询同 SQL；提取为 helper 让
    scan 与 verify 共用同一「当前版」定义，避免两侧漂移。
    """
    return list(
        db.scalars(
            select(PlanVersion)
            .join(Experiment, PlanVersion.experiment_id == Experiment.id)
            .where(
                Experiment.project_id == project_id,
                Experiment.deleted_at.is_(None),
                PlanVersion.version == Experiment.current_plan_version,
            )
        )
    )


def _verify_plan_entry(
    db: Session, *, project, entity: Experiment, plan: PlanVersion
) -> dict[str, Any]:
    """单个实验当前版 plan 的 DB↔FS 对账，返回带 ``bucket`` 字段的 entry。

    bucket ∈ {verified, mismatch, missing}；caller 按 flag 决定是否计 blocking。

    - **缺 plan_file_path / 文件不存在** → missing（需 materialize）
    - **两侧都存在、字节一致** → verified
    - **DB 已是 stub（``See file:`` / ``<!-- slim create:``）指向同一路径** →
      verified（A2-2 stub 化 / slim create 完成态；FS 是权威）
    - **两侧都存在、字节不同** → mismatch（需 materialize --force 覆盖 FS）

    路径口径与 ``plan_service._fs_plan_matches_payload``（A1-4 去重判据）
    一致：``project.workspace_path / experiment.plan_file_path``。
    """
    plan_file_path = getattr(entity, "plan_file_path", None)
    base = {
        "kind": "plan",
        "slug": str(entity.id),
        "version": plan.version,
        "plan_file_path": plan_file_path,
    }
    if not plan_file_path:
        return {
            **base,
            "bucket": "missing",
            "reason": "实验无 plan_file_path（DB 内联 plan 未物化）",
        }
    from pathlib import Path

    plan_path = Path(project.workspace_path) / plan_file_path
    if not plan_path.is_file():
        return {
            **base,
            "bucket": "missing",
            "reason": f"FS plan.md 不存在: {plan_path}（先 `map experiment plan materialize`）",
        }
    try:
        fs_text = plan_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            **base,
            "bucket": "missing",
            "reason": f"读取 FS plan.md 失败: {exc}",
        }
    db_text = plan.content_md or ""
    if db_text == fs_text:
        return {**base, "bucket": "verified", "form": "full"}
    # DB 侧已是 stub（A2-2 完成态 / slim create 现状）：DB 存自描述 stub，
    # FS 是权威；只要 stub 指向同一路径即视为已就位。
    stub_markers = (
        f"See file: {plan_file_path}",
        f"<!-- slim create: plan content lives in {plan_file_path}",
    )
    if any(m in db_text for m in stub_markers):
        return {**base, "bucket": "verified", "form": "stub"}
    # 两侧都在但字节不同：需物化覆盖 FS（creator 通道 materialize --force）。
    return {
        **base,
        "bucket": "mismatch",
        "fields": [
            {
                "field": "content_md",
                "db_sha256": hashlib.sha256(db_text.encode("utf-8")).hexdigest(),
                "fs_sha256": hashlib.sha256(fs_text.encode("utf-8")).hexdigest(),
                "db_len": len(db_text),
                "fs_len": len(fs_text),
            }
        ],
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
