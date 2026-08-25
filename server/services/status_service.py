import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentPhase
from server.domain.schemas import (
    ExperimentSummaryRead,
    GlobalStatusRead,
    WakerHeartbeatRead,
)
from server.services.project_service import build_projects_status, get_project, list_projects
from server.services.time_utils import as_utc

# T10（2026-08）：``GET /status`` 短 TTL 进程内缓存。看板每次刷新都会
# 触发跨项目聚合计数 + recent10 + 全部 agent 心跳扫描；缓存把高频刷新
# 摊到一次构建。TTL 由 ``MAP_STATUS_CACHE_TTL_SECONDS`` 控制（默认 5s，
# 0 = 关闭）。GlobalStatusRead 是只读快照模型，FastAPI 每请求仅做序列化
# 不会改动缓存实例；多 worker 部署各进程独立缓存，TTL 即最大陈旧窗口。
_status_cache: dict[uuid.UUID | None, tuple[float, GlobalStatusRead]] = {}
_status_cache_lock = threading.Lock()


def reset_status_cache() -> None:
    """清空 /status 缓存（测试隔离钩子，也可供运维脚本调用）。"""
    with _status_cache_lock:
        _status_cache.clear()


def _cached_status(project_id: uuid.UUID | None) -> GlobalStatusRead | None:
    from server.config import get_settings

    ttl = get_settings().status_cache_ttl_seconds
    if ttl <= 0:
        return None
    with _status_cache_lock:
        hit = _status_cache.get(project_id)
    if hit is None:
        return None
    stored_at, payload = hit
    if time.monotonic() - stored_at >= ttl:
        return None
    return payload


def _store_status(project_id: uuid.UUID | None, payload: GlobalStatusRead) -> None:
    from server.config import get_settings

    if get_settings().status_cache_ttl_seconds <= 0:
        return
    with _status_cache_lock:
        _status_cache[project_id] = (time.monotonic(), payload)


def build_waker_heartbeats(
    db: Session,
    *,
    project_id: uuid.UUID | None = None,
    threshold_minutes: int | None = None,
    now: datetime | None = None,
) -> list[WakerHeartbeatRead]:
    """Per-agent waker heartbeat rows with server-computed ``stale`` flag.

    stale = ever heartbeated (last_waker_poll_at not null) AND older than the
    threshold. null last_waker_poll_at → ``never``, NOT stale → no WARN, so the
    "only one waker" deployment (other personas never poll) stays quiet (D1 null
    semantics). Threshold falls back to ``Settings.waker_stale_threshold_minutes``
    (env ``MAP_WAKER_STALE_THRESHOLD_MINUTES``, default 15).
    """
    from server.config import get_settings

    if threshold_minutes is None:
        threshold_minutes = get_settings().waker_stale_threshold_minutes
    cutoff = as_utc(now or datetime.now(timezone.utc)) - timedelta(
        minutes=threshold_minutes
    )
    stmt = select(Agent)
    if project_id is not None:
        stmt = stmt.where(Agent.project_id == project_id)
    rows: list[WakerHeartbeatRead] = []
    for agent in db.scalars(stmt):
        last = as_utc(agent.last_waker_poll_at)
        stale = last is not None and last < cutoff
        rows.append(
            WakerHeartbeatRead(
                agent_id=agent.id,
                agent_name=agent.name,
                persona=agent.persona,
                last_waker_poll_at=last,
                stale=stale,
            )
        )
    return rows


def get_global_status(db: Session, *, project_id: uuid.UUID | None = None) -> GlobalStatusRead:
    """全局看板快照（T10：短 TTL 进程内缓存包装）。

    命中缓存直接返回快照；未命中或过期才走 ``_build_global_status`` 的
    全量构建（跨项目聚合 + recent10 + agent 心跳扫描）。
    """
    cached = _cached_status(project_id)
    if cached is not None:
        return cached
    payload = _build_global_status(db, project_id=project_id)
    _store_status(project_id, payload)
    return payload


def _build_global_status(
    db: Session, *, project_id: uuid.UUID | None = None
) -> GlobalStatusRead:
    if project_id is not None:
        project = get_project(db, project_id)
        project_status = build_projects_status(db, [project])[0]
        counts_stmt = (
            select(Experiment.phase, func.count())
            .where(
                Experiment.project_id == project_id,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
            )
            .group_by(Experiment.phase)
        )
        counts = {phase.value: count for phase, count in db.execute(counts_stmt)}
        for phase in ExperimentPhase:
            counts.setdefault(phase.value, 0)
        recent_stmt = (
            select(Experiment)
            .where(
                Experiment.project_id == project_id,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
            )
            .order_by(Experiment.updated_at.desc())
            .limit(10)
        )
        recent = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(recent_stmt)]
        return GlobalStatusRead(
            total_experiments_by_phase=counts,
            projects=[project_status],
            recent_experiments=recent,
            waker_heartbeats=build_waker_heartbeats(db, project_id=project_id),
        )

    projects = list_projects(db)
    project_statuses = build_projects_status(db, projects)

    counts_stmt = (
        select(Experiment.phase, func.count())
        .where(Experiment.deleted_at.is_(None), Experiment.archived_at.is_(None))
        .group_by(Experiment.phase)
    )
    counts = {phase.value: count for phase, count in db.execute(counts_stmt)}
    for phase in ExperimentPhase:
        counts.setdefault(phase.value, 0)

    recent_stmt = (
        select(Experiment)
        .where(Experiment.deleted_at.is_(None), Experiment.archived_at.is_(None))
        .order_by(Experiment.updated_at.desc())
        .limit(10)
    )
    recent = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(recent_stmt)]

    return GlobalStatusRead(
        total_experiments_by_phase=counts,
        projects=project_statuses,
        recent_experiments=recent,
        waker_heartbeats=build_waker_heartbeats(db),
    )
