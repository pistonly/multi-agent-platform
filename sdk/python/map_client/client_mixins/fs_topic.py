from __future__ import annotations

import uuid
from typing import Any

from map_types import (
    ExperimentSummaryRead,
    ExperimentUpdate,
    FsAdvanceRoundRequest,
    FsCloseRequest,
    FsExperimentRead,
    FsPlaneStatusRead,
    FsProjectionDeltaRequest,
    FsProjectionDeltaResult,
    FsProjectionInventoryRead,
    FsProjectionMetaRead,
    FsProjectionPushRequest,
    FsTopicDetailRead,
    FsTopicSummaryRead,
    FsWorkItemRead,
    FsWriteCommitRequest,
    FsWriteCommitResponse,
    FsWriteVerdictRead,
    TopicAdvanceRound,
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
    TopicCreate,
    TopicDecisionRead,
    TopicRead,
    TopicReadCursorRead,
    TopicResolve,
    TopicStatus,
    TopicSummaryRead,
    TopicUpdate,
)


class FsTopicMixin:
    """topics CRUD + fs plane（map/ 文件夹事实源）资源域方法。"""

    # --- topics ---

    def create_topic(self, project_id: uuid.UUID, payload: TopicCreate) -> TopicSummaryRead:
        data = self._json("POST", f"/projects/{project_id}/topics", json=payload.model_dump())
        return TopicSummaryRead.model_validate(data)

    # --- fs plane（map/ 文件夹事实源）---

    def list_fs_topics(self, project_id: uuid.UUID) -> list[FsTopicSummaryRead]:
        data = self._json("GET", f"/projects/{project_id}/fs/topics")
        return [FsTopicSummaryRead.model_validate(item) for item in data]

    def get_fs_topic(self, project_id: uuid.UUID, slug: str) -> FsTopicDetailRead:
        return FsTopicDetailRead.model_validate(
            self._json("GET", f"/projects/{project_id}/fs/topics/{slug}")
        )

    def list_fs_experiments(self, project_id: uuid.UUID) -> list[FsExperimentRead]:
        data = self._json("GET", f"/projects/{project_id}/fs/experiments")
        return [FsExperimentRead.model_validate(item) for item in data]

    def fs_work(self, project_id: uuid.UUID, persona: str) -> list[FsWorkItemRead]:
        data = self._json(
            "GET", f"/projects/{project_id}/fs/work", params={"persona": persona}
        )
        return [FsWorkItemRead.model_validate(item) for item in data]

    def fs_advance_round(
        self,
        project_id: uuid.UUID,
        slug: str,
        payload: FsAdvanceRoundRequest | None = None,
    ) -> FsTopicSummaryRead:
        body = (payload or FsAdvanceRoundRequest()).model_dump(mode="json")
        data = self._json(
            "POST", f"/projects/{project_id}/fs/topics/{slug}/advance-round", json=body
        )
        return FsTopicSummaryRead.model_validate(data)

    def fs_close_topic(
        self,
        project_id: uuid.UUID,
        slug: str,
        payload: FsCloseRequest | None = None,
    ) -> FsTopicSummaryRead:
        body = (payload or FsCloseRequest()).model_dump(mode="json", exclude_none=True)
        data = self._json(
            "POST", f"/projects/{project_id}/fs/topics/{slug}/close", json=body
        )
        return FsTopicSummaryRead.model_validate(data)

    # --- fs plane：部署矩阵握手 / validate+commit / 投影上行 ---

    def fs_plane_status(self, project_id: uuid.UUID) -> FsPlaneStatusRead:
        """workspace 可达性握手：local-fs / projection-cache / detached。"""
        return FsPlaneStatusRead.model_validate(
            self._json("GET", f"/projects/{project_id}/fs/status")
        )

    def fs_projection_meta(self, project_id: uuid.UUID) -> FsProjectionMetaRead | None:
        data = self._json("GET", f"/projects/{project_id}/fs/projection")
        return FsProjectionMetaRead.model_validate(data) if data is not None else None

    def fs_validate_advance_round(
        self,
        project_id: uuid.UUID,
        slug: str,
        payload: FsAdvanceRoundRequest | None = None,
    ) -> FsWriteVerdictRead:
        body = (payload or FsAdvanceRoundRequest()).model_dump(
            mode="json", exclude_none=True
        )
        data = self._json(
            "POST",
            f"/projects/{project_id}/fs/topics/{slug}/advance-round/validate",
            json=body,
        )
        return FsWriteVerdictRead.model_validate(data)

    def fs_validate_close(
        self,
        project_id: uuid.UUID,
        slug: str,
        payload: FsCloseRequest | None = None,
    ) -> FsWriteVerdictRead:
        body = (payload or FsCloseRequest()).model_dump(
            mode="json", exclude_none=True
        )
        data = self._json(
            "POST",
            f"/projects/{project_id}/fs/topics/{slug}/close/validate",
            json=body,
        )
        return FsWriteVerdictRead.model_validate(data)

    def fs_write_commit(
        self,
        project_id: uuid.UUID,
        payload: FsWriteCommitRequest,
    ) -> FsWriteCommitResponse:
        data = self._json(
            "POST", f"/projects/{project_id}/fs/write-commit", json=payload.model_dump(mode="json")
        )
        return FsWriteCommitResponse.model_validate(data)

    def fs_push_projection(
        self,
        project_id: uuid.UUID,
        payload: FsProjectionPushRequest,
    ) -> FsProjectionMetaRead:
        data = self._json(
            "PUT", f"/projects/{project_id}/fs/projection", json=payload.model_dump(mode="json")
        )
        return FsProjectionMetaRead.model_validate(data)

    def fs_projection_inventory(
        self, project_id: uuid.UUID
    ) -> FsProjectionInventoryRead | None:
        data = self._json("GET", f"/projects/{project_id}/fs/projection/inventory")
        if data is None:
            return None
        return FsProjectionInventoryRead.model_validate(data)

    def fs_apply_projection_delta(
        self,
        project_id: uuid.UUID,
        payload: FsProjectionDeltaRequest,
    ) -> FsProjectionDeltaResult:
        data = self._json(
            "POST",
            f"/projects/{project_id}/fs/projection/delta",
            json=payload.model_dump(mode="json"),
        )
        return FsProjectionDeltaResult.model_validate(data)

    def list_topics(
        self,
        project_id: uuid.UUID,
        *,
        status: TopicStatus | None = None,
        creator_agent_id: uuid.UUID | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_archived: bool = False,
    ) -> list[TopicSummaryRead]:
        data, _total = self.list_topics_page(
            project_id,
            status=status,
            creator_agent_id=creator_agent_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
        return data

    def list_topics_page(
        self,
        project_id: uuid.UUID,
        *,
        status: TopicStatus | None = None,
        creator_agent_id: uuid.UUID | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_archived: bool = False,
    ) -> tuple[list[TopicSummaryRead], int]:
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "include_archived": include_archived,
        }
        if status is not None:
            params["status"] = status.value
        if creator_agent_id is not None:
            params["creator_agent_id"] = str(creator_agent_id)
        if q:
            params["q"] = q
        response = self._request("GET", f"/projects/{project_id}/topics", params=params)
        data = response.json()
        return [TopicSummaryRead.model_validate(item) for item in data], self._total_count(response)

    def get_topic(self, topic_id: uuid.UUID) -> TopicRead:
        return TopicRead.model_validate(self._json("GET", f"/topics/{topic_id}"))

    def resolve_topic(self, topic_id: uuid.UUID, payload: TopicResolve) -> TopicDecisionRead:
        data = self._json("POST", f"/topics/{topic_id}/resolve", json=payload.model_dump(mode="json"))
        return TopicDecisionRead.model_validate(data)

    def advance_topic_round(self, topic_id: uuid.UUID, payload: TopicAdvanceRound | None = None) -> TopicSummaryRead:
        body = (payload or TopicAdvanceRound()).model_dump(mode="json")
        data = self._json("POST", f"/topics/{topic_id}/advance-round", json=body)
        return TopicSummaryRead.model_validate(data)

    def rollback_topic_round(self, topic_id: uuid.UUID) -> TopicSummaryRead:
        data = self._json("POST", f"/topics/{topic_id}/rollback-round")
        return TopicSummaryRead.model_validate(data)

    def update_topic(self, topic_id: uuid.UUID, payload: TopicUpdate) -> TopicSummaryRead:
        data = self._json("PATCH", f"/topics/{topic_id}", json=payload.model_dump(exclude_unset=True))
        return TopicSummaryRead.model_validate(data)

    def update_experiment(
        self, experiment_id: uuid.UUID, payload: ExperimentUpdate
    ) -> ExperimentSummaryRead:
        data = self._json(
            "PATCH",
            f"/experiments/{experiment_id}",
            json=payload.model_dump(exclude_unset=True),
        )
        return ExperimentSummaryRead.model_validate(data)

    def delete_topic(self, topic_id: uuid.UUID) -> None:
        self._request("DELETE", f"/topics/{topic_id}")

    def close_topic(
        self,
        topic_id: uuid.UUID,
        *,
        close_reason: str | None = None,
        close_note: str | None = None,
    ) -> TopicSummaryRead:
        body: dict[str, object] = {}
        if close_reason is not None:
            body["close_reason"] = close_reason
        if close_note is not None:
            body["close_note"] = close_note
        kwargs = {"json": body} if body else {}
        return TopicSummaryRead.model_validate(
            self._json("POST", f"/topics/{topic_id}/close", **kwargs)
        )

    def reopen_topic(self, topic_id: uuid.UUID) -> TopicSummaryRead:
        return TopicSummaryRead.model_validate(self._json("POST", f"/topics/{topic_id}/reopen"))

    def dismiss_topic(self, topic_id: uuid.UUID) -> TopicSummaryRead:
        return TopicSummaryRead.model_validate(self._json("POST", f"/topics/{topic_id}/dismiss"))

    def mark_topic_read(self, topic_id: uuid.UUID) -> TopicReadCursorRead:
        data = self._json("POST", f"/agents/me/topics/{topic_id}/read")
        return TopicReadCursorRead.model_validate(data)

    def create_topic_comment(
        self,
        topic_id: uuid.UUID,
        payload: TopicCommentCreate,
    ) -> TopicCommentRead:
        data = self._json("POST", f"/topics/{topic_id}/comments", json=payload.model_dump(mode="json"))
        return TopicCommentRead.model_validate(data)

    def list_topic_comments(
        self,
        topic_id: uuid.UUID,
        *,
        tree: bool = False,
    ) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
        data = self._json("GET", f"/topics/{topic_id}/comments", params={"tree": tree})
        if tree:
            return [TopicCommentTreeNode.model_validate(item) for item in data]
        return [TopicCommentRead.model_validate(item) for item in data]
