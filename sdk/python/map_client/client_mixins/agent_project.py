from __future__ import annotations

import uuid
from typing import Any

from map_types import (
    ActionItemCancel,
    AgentCreateResponse,
    AgentRead,
    AgentRole,
    EscalationTargetRead,
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectStatusRevise,
    ProjectStatusVersionRead,
    ProjectUpdate,
    TopicActionItemRead,
    TopicActionItemStatus,
    TopicDecisionRead,
)


class AgentProjectMixin:
    """agents + projects 资源域方法。"""

    # --- agents ---

    def list_agents(
        self,
        *,
        project_id: uuid.UUID | None = None,
        role: AgentRole | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[AgentRead]:
        params: dict[str, str] = {}
        if project_id is not None:
            params["project_id"] = str(project_id)
        if role is not None:
            params["role"] = role.value
        if page is not None:
            params["page"] = str(page)
        if page_size is not None:
            params["page_size"] = str(page_size)
        data = self._json("GET", "/agents", params=params)
        return [AgentRead.model_validate(item) for item in data]

    def register_agent(
        self,
        name: str,
        role: str = "agent",
        *,
        project_key: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> AgentCreateResponse:
        # cleanup PR5: server expects a JSON body (AgentCreate), not query
        # params. ``json=`` carries the same fields and matches the new
        # contract.
        body: dict[str, object] = {"name": name, "role": role}
        if project_key is not None:
            body["project_key"] = project_key
        if project_id is not None:
            body["project_id"] = str(project_id)
        data = self._json("POST", "/agents", json=body)
        return AgentCreateResponse.model_validate(data)

    def get_me(self) -> AgentRead:
        return AgentRead.model_validate(self._json("GET", "/agents/me"))

    def get_escalation_target(
        self, experiment_id: uuid.UUID | None = None
    ) -> EscalationTargetRead:
        """Resolve the escalation contact for a STATE_MACHINE.* error.

        Calls ``GET /agents/me/escalation-target?experiment_id=...`` and
        returns the chosen agent + the tier that picked it. Used by the
        CLI on ``MAPHTTPError`` to append ``Escalation: @<name>`` to the
        error output.

        Args:
            experiment_id: When provided, the experiment's
                ``escalation_target_agent_id`` override takes precedence.
                When None, the role-based fallback chain runs without it.
        """
        params: dict[str, Any] = {}
        if experiment_id is not None:
            params["experiment_id"] = str(experiment_id)
        return EscalationTargetRead.model_validate(
            self._json("GET", "/agents/me/escalation-target", params=params or None)
        )

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
        content_root: str = "map",
        fs_freshness_sla_seconds: int | None = None,
    ) -> ProjectRead:
        payload = ProjectCreate(
            project_key=project_key,
            name=name,
            workspace_path=workspace_path,
            description=description,
            content_root=content_root,
            fs_freshness_sla_seconds=fs_freshness_sla_seconds,
        )
        data = self._json("POST", "/projects", json=payload.model_dump())
        return ProjectRead.model_validate(data)

    def list_projects(
        self,
        *,
        include_archived: bool = False,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[ProjectRead]:
        params: dict[str, str] = {"include_archived": str(include_archived)}
        if page is not None:
            params["page"] = str(page)
        if page_size is not None:
            params["page_size"] = str(page_size)
        data = self._json("GET", "/projects", params=params)
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

    def list_project_decisions(self, project_id: uuid.UUID, *, limit: int = 20) -> list[TopicDecisionRead]:
        data = self._json("GET", f"/projects/{project_id}/decisions", params={"limit": limit})
        return [TopicDecisionRead.model_validate(item) for item in data]

    def list_project_action_items(
        self,
        project_id: uuid.UUID,
        *,
        owner_agent_id: uuid.UUID | None = None,
        status: TopicActionItemStatus | None = None,
        limit: int = 100,
    ) -> list[TopicActionItemRead]:
        params: dict[str, Any] = {"limit": limit}
        if owner_agent_id is not None:
            params["owner_agent_id"] = str(owner_agent_id)
        if status is not None:
            params["status"] = status.value
        data = self._json("GET", f"/projects/{project_id}/action-items", params=params)
        return [TopicActionItemRead.model_validate(item) for item in data]

    def complete_action_item(self, action_item_id: uuid.UUID) -> TopicActionItemRead:
        data = self._json("POST", f"/action-items/{action_item_id}/complete")
        return TopicActionItemRead.model_validate(data)

    def deliver_action_item(self, action_item_id: uuid.UUID) -> TopicActionItemRead:
        data = self._json("POST", f"/action-items/{action_item_id}/deliver")
        return TopicActionItemRead.model_validate(data)

    def cancel_action_item(
        self,
        action_item_id: uuid.UUID,
        payload: ActionItemCancel,
    ) -> TopicActionItemRead:
        data = self._json(
            "POST",
            f"/action-items/{action_item_id}/cancel",
            json=payload.model_dump(exclude_none=True),
        )
        return TopicActionItemRead.model_validate(data)

    def link_action_item(
        self,
        action_item_id: uuid.UUID,
        experiment_id: uuid.UUID,
    ) -> TopicActionItemRead:
        data = self._json(
            "POST",
            f"/action-items/{action_item_id}/link",
            params={"experiment_id": str(experiment_id)},
        )
        return TopicActionItemRead.model_validate(data)

    def mark_wake_sent(self, action_item_id: uuid.UUID) -> TopicActionItemRead:
        """Bump ``wake_count`` + stamp ``last_woken_at`` + audit row.

        Experiment B / I4 — called by the runtime-waker CLI process when
        ``should_wake_action_item`` returns ``'wake'``. Owner or admin only.
        """
        data = self._json("POST", f"/action-items/{action_item_id}/mark-wake-sent")
        return TopicActionItemRead.model_validate(data)

    def mark_stale(self, action_item_id: uuid.UUID) -> TopicActionItemRead:
        """Stamp ``stale_at`` + write the ``action_item.stale`` audit row.

        Experiment B / I4 — called by the runtime-waker CLI process when
        ``should_wake_action_item`` returns ``'stale'`` (4th unanswered wake).
        Admin only.
        """
        data = self._json("POST", f"/action-items/{action_item_id}/mark-stale")
        return TopicActionItemRead.model_validate(data)

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
