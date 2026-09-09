"""DB → FS projection 存量迁移 SDK mixin（实验 M3 A2）。

五个方法：scan / plan / execute / status / verify，一一对应
``/projects/{pid}/fs-migration/*`` 端点。权限（host persona / admin）
由 server 端二次 gate，SDK 不预判——让 401/403 透传，避免权限矩阵
双源漂移。

返回均为 plain dict（迁移报告是一次性运维输出，不值得建 read schema
家族；字段契约由 server response 定义 + CLI 测试钉住）。
"""

from __future__ import annotations

import uuid


class FsMigrationMixin:
    """project-scoped 存量迁移 manifest 域方法。"""

    def fs_migration_scan(self, project_id: uuid.UUID) -> dict:
        return self._json("POST", f"/projects/{project_id}/fs-migration/scan")

    def fs_migration_plan(
        self, project_id: uuid.UUID, limit: int = 50
    ) -> dict:
        return self._json(
            "GET",
            f"/projects/{project_id}/fs-migration/plan",
            params={"limit": limit},
        )

    def fs_migration_execute(
        self, project_id: uuid.UUID, *, apply: bool, limit: int = 50
    ) -> dict:
        return self._json(
            "POST",
            f"/projects/{project_id}/fs-migration/execute",
            json={"apply": apply, "limit": limit},
        )

    def fs_migration_status(self, project_id: uuid.UUID) -> dict:
        return self._json("GET", f"/projects/{project_id}/fs-migration/status")

    def fs_migration_verify(self, project_id: uuid.UUID) -> dict:
        return self._json("POST", f"/projects/{project_id}/fs-migration/verify")
