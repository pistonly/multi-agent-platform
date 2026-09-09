"""DB → FS projection 存量迁移 manifest API（实验 M3 A2 收口）。

历史（M2 I5/I6）的 manifest 读写活在 CLI 进程里、直连 server DB——绕过
API 层的权限/审计，且 CLI 在非 server 主机上会静默新建空库（scan 报
``scanned=0`` 假成功）。本模块把 manifest 四操作收进 server API，CLI 改走
HTTP：

- ``POST /projects/{project_id}/fs-migration/scan`` —— 枚举 DB 实体建
  manifest item（幂等）。
- ``GET  /projects/{project_id}/fs-migration/plan`` —— 只读 actionable
  预览（dry-run 用，零写副作用）。
- ``POST /projects/{project_id}/fs-migration/execute`` —— 逐 item claim /
  apply / mark，**逐 item 提交事务**（断点真实存在）；``apply=true`` 走
  fs_projection delta 真 CAS。
- ``GET  /projects/{project_id}/fs-migration/status`` —— project 级汇总 +
  最近 run。
- ``POST /projects/{project_id}/fs-migration/verify`` —— manifest applied
  项 vs server FS 视角按 A2 字段契约逐项比对。

权限：project host persona 或 admin（与 feature flag set 门禁同源语义）；
非授权 403。操作全部落 server audit 由既有 audit_service 中间件处理。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession

from server.api.background_tasks import bind_background_tasks
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.services import migration_manifest_service as svc
from server.services import permissions as perm

router = APIRouter(
    prefix="/projects",
    tags=["fs-migration"],
    dependencies=[Depends(bind_background_tasks)],
)


class FsMigrationExecuteRequest(BaseModel):
    apply: bool = Field(
        default=False,
        description="false=dry 排练（记 skipped，不写 server）；true=真 CAS apply",
    )
    limit: int = Field(default=50, ge=1, le=500)


def _ensure_migration_operator(db: OrmSession, agent: Agent, project_id: uuid.UUID) -> None:
    """迁移是 project 级重操作：host persona 或 admin（同 flag set 门禁）。"""
    from map_types.persona import persona_from_agent_name

    from server.services.errors import ForbiddenError

    perm.ensure_project_access(agent, project_id)
    if agent.role.value == "admin":
        return
    if agent.project_id != project_id:
        raise ForbiddenError(
            f"fs-migration requires project host or admin; actor {agent.id} "
            f"belongs to project {agent.project_id}, target project is {project_id}"
        )
    persona = persona_from_agent_name(agent.name)
    if persona != "host":
        raise ForbiddenError(
            f"fs-migration requires project host or admin; actor {agent.id} "
            f"persona={persona!r}"
        )


def _project_or_404(db: OrmSession, project_id: uuid.UUID):
    from server.domain.models import Project

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id} not found",
        )
    return project


@router.post("/{project_id}/fs-migration/scan")
def scan_migration_manifest(
    project_id: uuid.UUID,
    db: OrmSession = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> dict:
    _ensure_migration_operator(db, agent, project_id)
    _project_or_404(db, project_id)
    report = svc.scan_project(db, project_id=project_id)
    db.commit()
    return {
        "project_id": str(project_id),
        "run_id": report.run_id,
        "scanned": report.scanned,
        "inserted": report.inserted,
        "skipped_existing": report.skipped_existing,
        "by_kind": report.by_kind,
        "by_status": report.by_status,
    }


@router.get("/{project_id}/fs-migration/plan")
def plan_migration(
    project_id: uuid.UUID,
    limit: int = 50,
    db: OrmSession = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> dict:
    _ensure_migration_operator(db, agent, project_id)
    _project_or_404(db, project_id)
    return svc.plan_project(db, project_id=project_id, limit=min(limit, 500))


@router.post("/{project_id}/fs-migration/execute")
def execute_migration(
    project_id: uuid.UUID,
    payload: FsMigrationExecuteRequest,
    db: OrmSession = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> dict:
    _ensure_migration_operator(db, agent, project_id)
    project = _project_or_404(db, project_id)
    return svc.execute_pending(
        db, project=project, agent=agent, apply=payload.apply, limit=payload.limit
    )


@router.get("/{project_id}/fs-migration/status")
def migration_status(
    project_id: uuid.UUID,
    db: OrmSession = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> dict:
    _ensure_migration_operator(db, agent, project_id)
    _project_or_404(db, project_id)
    run = svc.get_latest_run(db, project_id=project_id)
    return {
        "project_id": str(project_id),
        "summary": svc.summarize_project(db, project_id=project_id),
        "latest_run": (
            {
                "run_id": run.id,
                "phase": run.phase,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
                "summary": run.summary,
            }
            if run is not None
            else None
        ),
    }


@router.post("/{project_id}/fs-migration/verify")
def verify_migration(
    project_id: uuid.UUID,
    db: OrmSession = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> dict:
    _ensure_migration_operator(db, agent, project_id)
    project = _project_or_404(db, project_id)
    return svc.verify_project(db, project=project)
