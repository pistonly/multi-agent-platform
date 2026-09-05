"""Project feature flag SDK mixin (实验 M2 I4：A4).

三个方法：list / get / set。set 强制 host creator / admin（服务端
``feature_flag_service._ensure_can_set_flag`` 二次 gate），SDK 这里
不预判权限——让 server 401/403 路径透传给 caller，避免 SDK 与 server
权限矩阵漂移。

约定：flag_key 走 ``map_types.schemas.FLAG_FS_STOP_DUPLICATE_INSERT``
常量；set 的 value 必须是字面 ``"on"`` / ``"off"``（schema Literal）。
"""

from __future__ import annotations

import uuid

from map_types import ProjectFeatureFlagRead, ProjectFeatureFlagSet


class FeatureFlagMixin:
    """project-scoped feature flag 域方法。"""

    def list_project_feature_flags(
        self, project_id: uuid.UUID
    ) -> list[ProjectFeatureFlagRead]:
        data = self._json("GET", f"/projects/{project_id}/feature-flags")
        return [ProjectFeatureFlagRead.model_validate(item) for item in data]

    def get_project_feature_flag(
        self, project_id: uuid.UUID, flag_key: str
    ) -> ProjectFeatureFlagRead | None:
        """读单条 flag；不存在返 None（不要 raise — caller 走 default）。"""
        from map_client.exceptions import MAPNotFoundError

        try:
            data = self._json(
                "GET", f"/projects/{project_id}/feature-flags/{flag_key}"
            )
        except MAPNotFoundError:
            return None
        return ProjectFeatureFlagRead.model_validate(data)

    def set_project_feature_flag(
        self,
        project_id: uuid.UUID,
        flag_key: str,
        payload: ProjectFeatureFlagSet,
    ) -> ProjectFeatureFlagRead:
        """upsert 一条 flag（host creator / admin only）。"""
        data = self._json(
            "PUT",
            f"/projects/{project_id}/feature-flags/{flag_key}",
            json=payload.model_dump(mode="json", exclude_none=True),
        )
        return ProjectFeatureFlagRead.model_validate(data)
