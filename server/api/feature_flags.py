"""Project feature flag API（实验 M2 I4：A4）。

端点：

- ``GET /projects/{project_id}/feature-flags`` —— 列 project 下所有已
  set 的 flag（空 list 表示全 default）。
- ``GET /projects/{project_id}/feature-flags/{flag_key}`` —— 读单条
  flag；不存在返 404（让 SDK catch 后转 None）。
- ``PUT /projects/{project_id}/feature-flags/{flag_key}`` —— upsert
  一条 flag（host creator / admin only；非授权 actor 403）。

权限语义统一在 service 层 ``feature_flag_service._ensure_can_set_flag``
集中表达；API 层只做 project access 检查与把 service 异常翻译成 HTTP
状态码。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import ProjectFeatureFlagRead, ProjectFeatureFlagSet
from server.services import feature_flag_service as svc
from server.services import permissions as perm

router = APIRouter(
    prefix="/projects",
    tags=["feature-flags"],
    dependencies=[Depends(bind_background_tasks)],
)


def _to_read_schema(row) -> ProjectFeatureFlagRead:
    return ProjectFeatureFlagRead.model_validate(
        {
            "project_id": row.project_id,
            "flag_key": row.flag_key,
            "flag_value": row.flag_value,
            "set_by_agent_id": row.set_by_agent_id,
            "set_at": row.set_at,
            "reason": row.reason,
        }
    )


@router.get(
    "/{project_id}/feature-flags",
    response_model=list[ProjectFeatureFlagRead],
)
def list_project_feature_flags(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ProjectFeatureFlagRead]:
    perm.ensure_project_access(agent, project_id)
    rows = svc.list_flags(db, project_id)
    return [_to_read_schema(r) for r in rows]


@router.get(
    "/{project_id}/feature-flags/{flag_key}",
    response_model=ProjectFeatureFlagRead,
)
def get_project_feature_flag(
    project_id: uuid.UUID,
    flag_key: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectFeatureFlagRead:
    perm.ensure_project_access(agent, project_id)
    row = svc.get_flag(db, project_id, flag_key)
    if row is None:
        # 用 HTTPException 让全局 error envelope 接管（不受 response_model
        # 强约束）；SDK MAPNotFoundError 路径自然处理。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"flag {flag_key!r} not set on project {project_id}",
        )
    return _to_read_schema(row)


@router.put(
    "/{project_id}/feature-flags/{flag_key}",
    response_model=ProjectFeatureFlagRead,
)
def set_project_feature_flag(
    project_id: uuid.UUID,
    flag_key: str,
    payload: ProjectFeatureFlagSet,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectFeatureFlagRead:
    perm.ensure_project_access(agent, project_id)
    row = svc.set_flag(
        db,
        project_id=project_id,
        flag_key=flag_key,
        flag_value=payload.flag_value,
        actor=agent,
        reason=payload.reason,
    )
    return _to_read_schema(row)
