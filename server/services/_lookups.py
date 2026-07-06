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

from sqlalchemy.orm import Session

from server.domain.models import Project
from server.services.errors import NotFoundError


def get_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project
