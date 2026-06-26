from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Project, ProjectStatusVersion
from server.domain.schemas import ProjectStatusRevise, ProjectStatusVersionRead
from server.services.errors import NotFoundError


def _require_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


def default_status_template(*, project_key: str, created_at: datetime | None = None) -> str:
    ts = (created_at or datetime.now()).isoformat()
    return f"""# Current Status — {project_key}

## 当前目标

- （待填写）

## 阻塞 / 风险

- 无

## 下一步

- （待填写）

---
_最后更新：{ts} · 版本 v1_

> 实验/话题清单由 `get_project_status` 快照字段提供（`active_experiments`、`recent_experiments` 等），勿在本 MD 中维护。
"""


def create_initial_status(
    db: Session,
    *,
    project: Project,
    author_agent_id: uuid.UUID,
) -> ProjectStatusVersion:
    version = ProjectStatusVersion(
        project_id=project.id,
        version=1,
        content_md=default_status_template(project_key=project.project_key, created_at=project.created_at),
        author_agent_id=author_agent_id,
        change_note="初始版本",
    )
    project.current_status_version = 1
    db.add(version)
    return version


def get_current_status_md(db: Session, project_id: uuid.UUID) -> tuple[int, str | None, datetime | None]:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if project.current_status_version <= 0:
        return 0, None, None
    row = _get_status_version_row(db, project_id, project.current_status_version)
    if row is None:
        return project.current_status_version, None, None
    return row.version, row.content_md, row.created_at


def revise_status(
    db: Session,
    project_id: uuid.UUID,
    author_agent_id: uuid.UUID,
    payload: ProjectStatusRevise,
) -> ProjectStatusVersion:
    project = _require_project(db, project_id)
    new_version_number = project.current_status_version + 1
    version = ProjectStatusVersion(
        project_id=project.id,
        version=new_version_number,
        content_md=payload.content_md,
        author_agent_id=author_agent_id,
        change_note=payload.change_note,
    )
    project.current_status_version = new_version_number
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def list_status_versions(db: Session, project_id: uuid.UUID) -> list[ProjectStatusVersionRead]:
    _require_project(db, project_id)
    stmt = (
        select(ProjectStatusVersion)
        .where(ProjectStatusVersion.project_id == project_id)
        .order_by(ProjectStatusVersion.version.desc())
    )
    rows = list(db.scalars(stmt))
    return [ProjectStatusVersionRead.model_validate(row) for row in rows]


def get_status_version(db: Session, project_id: uuid.UUID, version: int) -> ProjectStatusVersionRead:
    _require_project(db, project_id)
    row = _get_status_version_row(db, project_id, version)
    if row is None:
        raise NotFoundError("Project status version not found")
    return ProjectStatusVersionRead.model_validate(row)


def _get_status_version_row(
    db: Session,
    project_id: uuid.UUID,
    version: int,
) -> ProjectStatusVersion | None:
    stmt = select(ProjectStatusVersion).where(
        ProjectStatusVersion.project_id == project_id,
        ProjectStatusVersion.version == version,
    )
    return db.scalar(stmt)
