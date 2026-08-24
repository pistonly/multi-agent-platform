"""Project / experiment lookup helpers shared across services.

从 ``project_service`` 抽出 ``get_project`` 以打破 ``project_service ↔ topic_service``
循环 import：``topic_service`` 顶部需要 ``get_project`` 做存在性检查，而
``project_service`` 顶部又 import ``topic_service`` 做聚合 read——双向顶部 import
会导致 ``ImportError: cannot import name 'get_project' from partially initialized
module``。把纯查询 helper 下沉到本无 service 依赖的模块后，``topic_service`` 不再
需要在顶部 import ``project_service``。

``project_service`` 仍 re-export ``get_project``，保持现有
``from server.services.project_service import get_project`` 调用方不变。
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Project
from server.services.errors import ConflictError, NotFoundError


def get_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


def ensure_workspace_unique(
    db: Session,
    *,
    workspace_path: str,
    content_root: str | None,
    exclude_project_id: uuid.UUID | None = None,
) -> Project | None:
    """``workspace_path + content_root`` 联合键唯一（3b7c2b44 A5）。

    local-fs project 会绑定一个 workspace 目录；同一 workspace+content_root
    被第二个 project 认领会让聚合操作（导出/归档/waker/权限）二义。命中即抛
    ConflictError(409) 并指明已属哪个 project；不同 content_root 允许
    （同一 repo 开多 project 合法）。返回已认领的 project（无则 None）。
    """
    dup = db.scalar(
        select(Project).where(
            Project.workspace_path == workspace_path,
            Project.content_root == (content_root or "map"),
        )
    )
    if dup is not None and (exclude_project_id is None or dup.id != exclude_project_id):
        raise ConflictError(
            f"workspace_path+content_root 已被 project '{dup.name}' "
            f"(key={dup.project_key}) 认领；同一 repo 想开多 project 需用不同 "
            "content_root，重复绑定需先处置存量（retired-surface 清理或 archive "
            "换主，见 docs/WORKSPACE-UNIQUENESS.md）。"
        )
    return dup
