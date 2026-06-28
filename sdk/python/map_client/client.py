from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx

from map_client.config import load_config
from map_client.exceptions import MAPHTTPError
from map_types import (
    AgentCreateResponse,
    AgentRead,
    AuditLogRead,
    CommentAnchorType,
    CommentCreate,
    CommentRead,
    CommentTreeNode,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentBundleRead,
    ExperimentLogCreate,
    ExperimentLogRead,
    ExperimentPhase,
    ExperimentSummaryRead,
    GlobalStatusRead,
    PlanRevise,
    PlanVersionRead,
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectStatusRevise,
    ProjectStatusVersionRead,
    ProjectUpdate,
    ReviewCreate,
    ReviewItemRead,
    ReviewItemStatus,
    ReviewItemUpdate,
    ReviewRead,
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
    TopicCreate,
    TopicRead,
    TopicStatus,
    TopicSummaryRead,
    TopicUpdate,
    NotificationListRead,
    NotificationRead,
    TodoRead,
    WebhookCreate,
    WebhookCreateResponse,
    WebhookDeliveryRead,
    WebhookRead,
)


class MAPClient:
    """HTTP client for the Multi-Agent Platform REST API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise ValueError("API token is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._transport = transport
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls, *, transport: httpx.BaseTransport | None = None) -> MAPClient:
        cfg = load_config()
        if not cfg.get("token"):
            raise ValueError("MAP_TOKEN not set and no token in ~/.map/config.yaml")
        return cls(cfg["api_url"], cfg["token"], transport=transport)

    @classmethod
    def from_config(
        cls,
        config_path: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> MAPClient:
        cfg = load_config(Path(config_path) if config_path else None)
        if not cfg.get("token"):
            raise ValueError("MAP_TOKEN not set and no token in config")
        return cls(cfg["api_url"], cfg["token"], transport=transport)

    def close(self) -> None:
        if self._transport is None:
            self._http.close()

    def __enter__(self) -> MAPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            detail = response.text
            if response.content:
                try:
                    payload = response.json()
                    if isinstance(payload, dict) and "detail" in payload:
                        detail = str(payload["detail"])
                except Exception:
                    detail = response.text
            raise MAPHTTPError(response.status_code, detail)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        if response.status_code == 204:
            return None
        return response.json()

    @staticmethod
    def _total_count(response: httpx.Response) -> int:
        raw = response.headers.get("X-Total-Count")
        if raw is None:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    # --- agents ---

    def register_agent(
        self,
        name: str,
        role: str = "agent",
        *,
        project_key: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> AgentCreateResponse:
        params: dict[str, str] = {"name": name, "role": role}
        if project_key is not None:
            params["project_key"] = project_key
        if project_id is not None:
            params["project_id"] = str(project_id)
        data = self._json("POST", "/agents", params=params)
        return AgentCreateResponse.model_validate(data)

    def get_me(self) -> AgentRead:
        return AgentRead.model_validate(self._json("GET", "/agents/me"))

    def resolve_project_id(
        self,
        project_id: uuid.UUID | None = None,
        *,
        project_key: str | None = None,
    ) -> uuid.UUID:
        if project_id is not None:
            return project_id
        if project_key is not None:
            return self.get_project_by_key(project_key).id
        me = self.get_me()
        if me.project_id is not None:
            return me.project_id
        raise ValueError("project_id or project_key is required")

    # --- projects ---

    def create_project(
        self,
        project_key: str,
        name: str,
        workspace_path: str,
        description: str | None = None,
    ) -> ProjectRead:
        payload = ProjectCreate(
            project_key=project_key,
            name=name,
            workspace_path=workspace_path,
            description=description,
        )
        data = self._json("POST", "/projects", json=payload.model_dump())
        return ProjectRead.model_validate(data)

    def list_projects(self, *, include_archived: bool = False) -> list[ProjectRead]:
        data = self._json("GET", "/projects", params={"include_archived": include_archived})
        return [ProjectRead.model_validate(item) for item in data]

    def get_project(self, project_id: uuid.UUID) -> ProjectRead:
        return ProjectRead.model_validate(self._json("GET", f"/projects/{project_id}"))

    def get_project_by_key(self, project_key: str) -> ProjectRead:
        return ProjectRead.model_validate(self._json("GET", f"/projects/by-key/{project_key}"))

    def update_project(self, project_id: uuid.UUID, payload: ProjectUpdate) -> ProjectRead:
        data = self._json("PATCH", f"/projects/{project_id}", json=payload.model_dump(exclude_unset=True))
        return ProjectRead.model_validate(data)

    def get_project_status(self, project_id: uuid.UUID) -> ProjectStatusRead:
        return ProjectStatusRead.model_validate(self._json("GET", f"/projects/{project_id}/status"))

    def revise_project_status(
        self,
        project_id: uuid.UUID,
        payload: ProjectStatusRevise,
    ) -> ProjectStatusVersionRead:
        data = self._json("POST", f"/projects/{project_id}/status/revisions", json=payload.model_dump())
        return ProjectStatusVersionRead.model_validate(data)

    def list_project_status_versions(self, project_id: uuid.UUID) -> list[ProjectStatusVersionRead]:
        data = self._json("GET", f"/projects/{project_id}/status/versions")
        return [ProjectStatusVersionRead.model_validate(item) for item in data]

    def get_project_status_version(
        self,
        project_id: uuid.UUID,
        version: int,
    ) -> ProjectStatusVersionRead:
        data = self._json("GET", f"/projects/{project_id}/status/versions/{version}")
        return ProjectStatusVersionRead.model_validate(data)

    # --- experiments ---

    def create_experiment(self, project_id: uuid.UUID, payload: ExperimentCreate) -> ExperimentSummaryRead:
        data = self._json("POST", f"/projects/{project_id}/experiments", json=payload.model_dump(mode="json"))
        return ExperimentSummaryRead.model_validate(data)

    def list_experiments(
        self,
        project_id: uuid.UUID,
        *,
        phase: ExperimentPhase | None = None,
        creator_agent_id: uuid.UUID | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_archived: bool = False,
    ) -> list[ExperimentSummaryRead]:
        data, _total = self.list_experiments_page(
            project_id,
            phase=phase,
            creator_agent_id=creator_agent_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
        return data

    def list_experiments_page(
        self,
        project_id: uuid.UUID,
        *,
        phase: ExperimentPhase | None = None,
        creator_agent_id: uuid.UUID | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 100,
        include_archived: bool = False,
    ) -> tuple[list[ExperimentSummaryRead], int]:
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "include_archived": include_archived,
        }
        if phase is not None:
            params["phase"] = phase.value
        if creator_agent_id is not None:
            params["creator_agent_id"] = str(creator_agent_id)
        if q:
            params["q"] = q
        response = self._request("GET", f"/projects/{project_id}/experiments", params=params)
        data = response.json()
        return [ExperimentSummaryRead.model_validate(item) for item in data], self._total_count(response)

    def get_experiment(self, experiment_id: uuid.UUID) -> ExperimentDetailRead:
        return ExperimentDetailRead.model_validate(self._json("GET", f"/experiments/{experiment_id}"))

    def get_experiment_bundle(self, experiment_id: uuid.UUID) -> ExperimentBundleRead:
        return ExperimentBundleRead.model_validate(self._json("GET", f"/experiments/{experiment_id}/bundle"))

    def delete_experiment(self, experiment_id: uuid.UUID) -> None:
        self._request("DELETE", f"/experiments/{experiment_id}")

    # --- phase transitions ---

    def submit_for_review(self, experiment_id: uuid.UUID) -> ExperimentSummaryRead:
        data = self._json("POST", f"/experiments/{experiment_id}/submit-review")
        return ExperimentSummaryRead.model_validate(data)

    def approve_experiment(self, experiment_id: uuid.UUID) -> ExperimentSummaryRead:
        data = self._json("POST", f"/experiments/{experiment_id}/approve")
        return ExperimentSummaryRead.model_validate(data)

    def withdraw_from_review(self, experiment_id: uuid.UUID) -> ExperimentSummaryRead:
        data = self._json("POST", f"/experiments/{experiment_id}/withdraw")
        return ExperimentSummaryRead.model_validate(data)

    def cancel_experiment(self, experiment_id: uuid.UUID) -> ExperimentSummaryRead:
        data = self._json("POST", f"/experiments/{experiment_id}/cancel")
        return ExperimentSummaryRead.model_validate(data)

    def start_experiment(self, experiment_id: uuid.UUID) -> ExperimentSummaryRead:
        data = self._json("POST", f"/experiments/{experiment_id}/start")
        return ExperimentSummaryRead.model_validate(data)

    def complete_experiment(self, experiment_id: uuid.UUID, payload: ExperimentComplete) -> ExperimentSummaryRead:
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/complete",
            json=payload.model_dump(),
        )
        return ExperimentSummaryRead.model_validate(data)

    # --- plans ---

    def list_plans(self, experiment_id: uuid.UUID) -> list[PlanVersionRead]:
        data = self._json("GET", f"/experiments/{experiment_id}/plans")
        return [PlanVersionRead.model_validate(item) for item in data]

    def get_plan(self, experiment_id: uuid.UUID, version: int) -> PlanVersionRead:
        return PlanVersionRead.model_validate(
            self._json("GET", f"/experiments/{experiment_id}/plans/{version}")
        )

    def revise_plan(self, experiment_id: uuid.UUID, payload: PlanRevise) -> PlanVersionRead:
        data = self._json("POST", f"/experiments/{experiment_id}/plans", json=payload.model_dump(mode="json"))
        return PlanVersionRead.model_validate(data)

    # --- reviews ---

    def create_review(self, experiment_id: uuid.UUID, payload: ReviewCreate) -> ReviewRead:
        data = self._json("POST", f"/experiments/{experiment_id}/reviews", json=payload.model_dump())
        review = ReviewRead.model_validate(data)
        return review

    def list_reviews(self, experiment_id: uuid.UUID) -> list[ReviewRead]:
        data = self._json("GET", f"/experiments/{experiment_id}/reviews")
        return [ReviewRead.model_validate(item) for item in data]

    def update_review_item(self, item_id: uuid.UUID, status: ReviewItemStatus) -> ReviewItemRead:
        payload = ReviewItemUpdate(status=status)
        data = self._json("PATCH", f"/review-items/{item_id}", json=payload.model_dump())
        return ReviewItemRead.model_validate(data)

    # --- comments ---

    def create_comment(self, experiment_id: uuid.UUID, payload: CommentCreate) -> CommentRead:
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/comments",
            json=payload.model_dump(mode="json"),
        )
        return CommentRead.model_validate(data)

    def list_comments(
        self,
        experiment_id: uuid.UUID,
        *,
        tree: bool = False,
    ) -> list[CommentRead] | list[CommentTreeNode]:
        data = self._json("GET", f"/experiments/{experiment_id}/comments", params={"tree": tree})
        if tree:
            return [CommentTreeNode.model_validate(item) for item in data]
        return [CommentRead.model_validate(item) for item in data]

    # --- logs ---

    def create_log(self, experiment_id: uuid.UUID, payload: ExperimentLogCreate) -> ExperimentLogRead:
        data = self._json("POST", f"/experiments/{experiment_id}/logs", json=payload.model_dump())
        return ExperimentLogRead.model_validate(data)

    def list_logs(self, experiment_id: uuid.UUID) -> list[ExperimentLogRead]:
        data = self._json("GET", f"/experiments/{experiment_id}/logs")
        return [ExperimentLogRead.model_validate(item) for item in data]

    # --- topics ---

    def create_topic(self, project_id: uuid.UUID, payload: TopicCreate) -> TopicSummaryRead:
        data = self._json("POST", f"/projects/{project_id}/topics", json=payload.model_dump())
        return TopicSummaryRead.model_validate(data)

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

    def update_topic(self, topic_id: uuid.UUID, payload: TopicUpdate) -> TopicSummaryRead:
        data = self._json("PATCH", f"/topics/{topic_id}", json=payload.model_dump(exclude_unset=True))
        return TopicSummaryRead.model_validate(data)

    def delete_topic(self, topic_id: uuid.UUID) -> None:
        self._request("DELETE", f"/topics/{topic_id}")

    def close_topic(self, topic_id: uuid.UUID) -> TopicSummaryRead:
        return TopicSummaryRead.model_validate(self._json("POST", f"/topics/{topic_id}/close"))

    def reopen_topic(self, topic_id: uuid.UUID) -> TopicSummaryRead:
        return TopicSummaryRead.model_validate(self._json("POST", f"/topics/{topic_id}/reopen"))

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

    # --- todos ---

    def get_todos(self) -> TodoRead:
        return TodoRead.model_validate(self._json("GET", "/agents/me/todos"))

    # --- notifications ---

    def list_notifications(
        self,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> NotificationListRead:
        data = self._json(
            "GET",
            "/agents/me/notifications",
            params={"unread_only": unread_only, "limit": limit, "offset": offset},
        )
        return NotificationListRead.model_validate(data)

    def mark_notification_read(self, notification_id: uuid.UUID) -> NotificationRead:
        data = self._json("POST", f"/notifications/{notification_id}/read")
        return NotificationRead.model_validate(data)

    def mark_all_notifications_read(self) -> dict[str, int]:
        return self._json("POST", "/agents/me/notifications/read-all")

    # --- webhooks (admin) ---

    def create_webhook(self, payload: WebhookCreate) -> WebhookCreateResponse:
        data = self._json("POST", "/webhooks", json=payload.model_dump(mode="json"))
        return WebhookCreateResponse.model_validate(data)

    def list_webhooks(self, project_id: uuid.UUID | None = None) -> list[WebhookRead]:
        params = {"project_id": str(project_id)} if project_id else None
        data = self._json("GET", "/webhooks", params=params)
        return [WebhookRead.model_validate(w) for w in data]

    def delete_webhook(self, webhook_id: uuid.UUID) -> None:
        self._request("DELETE", f"/webhooks/{webhook_id}")

    def list_webhook_deliveries(self, webhook_id: uuid.UUID) -> list[WebhookDeliveryRead]:
        data = self._json("GET", f"/webhooks/{webhook_id}/deliveries")
        return [WebhookDeliveryRead.model_validate(d) for d in data]

    # --- audit ---

    def list_audit_for_target(self, target_type: str, target_id: uuid.UUID) -> list[AuditLogRead]:
        data = self._json(
            "GET",
            "/audit",
            params={"target_type": target_type, "target_id": str(target_id)},
        )
        return [AuditLogRead.model_validate(a) for a in data]

    def list_audit_global(self, *, page: int = 1, page_size: int = 50) -> tuple[list[AuditLogRead], int]:
        resp = self._request("GET", "/admin/audit", params={"page": page, "page_size": page_size})
        items = [AuditLogRead.model_validate(a) for a in resp.json()]
        return items, int(resp.headers.get("X-Total-Count", len(items)))

    # --- status ---

    def get_global_status(self, project_id: uuid.UUID | None = None) -> GlobalStatusRead:
        params = {"project_id": str(project_id)} if project_id else None
        return GlobalStatusRead.model_validate(self._json("GET", "/status", params=params))


# Re-export types useful for SDK consumers
__all__ = [
    "MAPClient",
    "CommentAnchorType",
    "ExperimentPhase",
    "ReviewItemStatus",
    "ExperimentCreate",
    "ExperimentComplete",
    "ExperimentLogCreate",
    "PlanRevise",
    "ReviewCreate",
    "CommentCreate",
    "ProjectUpdate",
    "TopicCreate",
    "TopicUpdate",
    "TopicCommentCreate",
    "TopicStatus",
]
