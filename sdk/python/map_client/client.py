from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx

from map_client.config import load_config
from map_client.exceptions import MAPHTTPError
from server.domain.models import CommentAnchorType, ExperimentPhase, ReviewItemStatus
from server.domain.schemas import (
    AgentCreateResponse,
    AgentRead,
    CommentCreate,
    CommentRead,
    CommentTreeNode,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentLogCreate,
    ExperimentLogRead,
    ExperimentSummaryRead,
    GlobalStatusRead,
    PlanRevise,
    PlanVersionRead,
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectUpdate,
    ReviewCreate,
    ReviewItemRead,
    ReviewItemUpdate,
    ReviewRead,
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

    # --- agents ---

    def register_agent(self, name: str, role: str = "agent") -> AgentCreateResponse:
        data = self._json("POST", "/agents", params={"name": name, "role": role})
        return AgentCreateResponse.model_validate(data)

    def get_me(self) -> AgentRead:
        return AgentRead.model_validate(self._json("GET", "/agents/me"))

    # --- projects ---

    def create_project(self, name: str, workspace_path: str, description: str | None = None) -> ProjectRead:
        payload = ProjectCreate(name=name, workspace_path=workspace_path, description=description)
        data = self._json("POST", "/projects", json=payload.model_dump())
        return ProjectRead.model_validate(data)

    def list_projects(self, *, include_archived: bool = False) -> list[ProjectRead]:
        data = self._json("GET", "/projects", params={"include_archived": include_archived})
        return [ProjectRead.model_validate(item) for item in data]

    def get_project(self, project_id: uuid.UUID) -> ProjectRead:
        return ProjectRead.model_validate(self._json("GET", f"/projects/{project_id}"))

    def update_project(self, project_id: uuid.UUID, payload: ProjectUpdate) -> ProjectRead:
        data = self._json("PATCH", f"/projects/{project_id}", json=payload.model_dump(exclude_unset=True))
        return ProjectRead.model_validate(data)

    def get_project_status(self, project_id: uuid.UUID) -> ProjectStatusRead:
        return ProjectStatusRead.model_validate(self._json("GET", f"/projects/{project_id}/status"))

    # --- experiments ---

    def create_experiment(self, project_id: uuid.UUID, payload: ExperimentCreate) -> ExperimentSummaryRead:
        data = self._json("POST", f"/projects/{project_id}/experiments", json=payload.model_dump())
        return ExperimentSummaryRead.model_validate(data)

    def list_experiments(
        self,
        project_id: uuid.UUID,
        *,
        phase: ExperimentPhase | None = None,
    ) -> list[ExperimentSummaryRead]:
        params = {"phase": phase.value} if phase else None
        data = self._json("GET", f"/projects/{project_id}/experiments", params=params)
        return [ExperimentSummaryRead.model_validate(item) for item in data]

    def get_experiment(self, experiment_id: uuid.UUID) -> ExperimentDetailRead:
        return ExperimentDetailRead.model_validate(self._json("GET", f"/experiments/{experiment_id}"))

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
        data = self._json("POST", f"/experiments/{experiment_id}/plans", json=payload.model_dump())
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
]
