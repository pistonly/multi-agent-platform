from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from map_types import (
    ActionItemCancel,
    AgentCreateResponse,
    AgentRead,
    AgentRole,
    AgentWorkRead,
    AgentWorkSummaryRead,
    AuditLogRead,
    CommentAnchorType,
    CommentCreate,
    CommentRead,
    CommentTreeNode,
    CrossPersonaCallRecord,
    DismissAllMentionsResultRead,
    DismissMentionResultRead,
    EscalationTargetRead,
    ExperimentBundleRead,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentLockRead,
    ExperimentLockStalledScanRead,
    ExperimentLogCreate,
    ExperimentLogRead,
    ExperimentPhase,
    ExperimentResultDecision,
    ExperimentSummaryRead,
    ExperimentUpdate,
    FeedbackCategory,
    FeedbackStatus,
    FsAdvanceRoundRequest,
    FsCloseRequest,
    FsExperimentRead,
    FsPlaneStatusRead,
    FsProjectionMetaRead,
    FsProjectionPushRequest,
    FsTopicDetailRead,
    FsTopicSummaryRead,
    FsWorkItemRead,
    FsWriteCommitRequest,
    FsWriteCommitResponse,
    FsWriteVerdictRead,
    GlobalStatusRead,
    InboundEventCreate,
    InboundEventRecordResult,
    LogCreateResponse,
    NotificationCategory,
    NotificationListRead,
    NotificationRead,
    PlanRevise,
    PlanVersionRead,
    PlatformFeedbackCreate,
    PlatformFeedbackRead,
    PlatformFeedbackUpdate,
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
    TodoRead,
    TopicActionItemRead,
    TopicActionItemStatus,
    TopicAdvanceRound,
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
    TopicCreate,
    TopicDecisionRead,
    TopicProgressListRead,
    TopicRead,
    TopicReadCursorRead,
    TopicResolve,
    TopicStatus,
    TopicSummaryRead,
    TopicUpdate,
    WebhookCreate,
    WebhookCreateResponse,
    WebhookDeliveryRead,
    WebhookRead,
)

from map_client.config import load_config
from map_client.exceptions import raise_for_status

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _is_local_url(url: str) -> bool:
    """判断 URL 的 host 是否指向本机。

    localhost / 127.0.0.1 / ::1 / 未指定 host 时返回 True。此类场景应
    忽略环境变量代理，避免因 SOCKS 代理初始化失败等问题影响对本地 MAP
    服务的访问。
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _LOCAL_HOSTS


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
        # 本地地址（localhost / 127.0.0.1 / ::1）忽略环境代理，避免因
        # SOCKS 代理初始化失败等影响本地 MAP 服务访问；其他地址沿用
        # 环境代理配置。transport 已显式指定时 trust_env 不生效。
        trust_env = not _is_local_url(self.base_url) if transport is None else True
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
            trust_env=trust_env,
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
            error_code: str | None = None
            hint: str | None = None
            retryable: bool | None = None
            if response.content:
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        if "detail" in payload:
                            detail = str(payload["detail"])
                        # I1(d): surface server-side subcodes so callers can
                        # branch on structured failure modes instead of
                        # pattern-matching the human-readable ``detail``.
                        if isinstance(payload.get("error_code"), str):
                            error_code = payload["error_code"]
                        if isinstance(payload.get("hint"), str):
                            hint = payload["hint"]
                        if isinstance(payload.get("retryable"), bool):
                            retryable = payload["retryable"]
                except Exception:
                    detail = response.text
            # P2 #2: 根据 status_code raise 具体子类（NotFound / Conflict 等），
            # 调用方可 catch 子类写语义化处理，不再依赖 if status_code == 404。
            raise_for_status(
                response.status_code,
                detail,
                error_code=error_code,
                hint=hint,
                retryable=retryable,
            )
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

    def list_agents(
        self,
        *,
        project_id: uuid.UUID | None = None,
        role: AgentRole | None = None,
    ) -> list[AgentRead]:
        params: dict[str, str] = {}
        if project_id is not None:
            params["project_id"] = str(project_id)
        if role is not None:
            params["role"] = role.value
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
        id_prefix: str | None = None,
    ) -> tuple[list[ExperimentSummaryRead], int]:
        """List experiments (one page).

        v0.12 M54B (E2): ``id_prefix`` (8..32 hex chars) resolves a short
        UUID prefix server-side via ``CAST(id AS CHAR) LIKE '<prefix>%'``.
        """
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
        if id_prefix:
            params["id_prefix"] = id_prefix.strip().lower().replace("-", "")
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

    def start_experiment(
        self,
        experiment_id: uuid.UUID,
        executor_agent_id: uuid.UUID | None = None,
    ) -> ExperimentSummaryRead:
        """Start experiment execution, optionally delegating to another agent.

        Migration 042: when ``executor_agent_id`` is provided, that agent
        becomes the sole non-admin caller allowed to ``complete`` the
        experiment. When omitted, the host self-executes.
        """
        json_body: dict[str, Any] | None = None
        if executor_agent_id is not None:
            json_body = {"executor_agent_id": str(executor_agent_id)}
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/start",
            json=json_body,
        )
        return ExperimentSummaryRead.model_validate(data)

    def complete_experiment(self, experiment_id: uuid.UUID, payload: ExperimentComplete) -> ExperimentSummaryRead:
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/complete",
            json=payload.model_dump(mode="json"),
        )
        return ExperimentSummaryRead.model_validate(data)

    def accept_experiment_result(
        self,
        experiment_id: uuid.UUID,
        payload: ExperimentResultDecision,
    ) -> ExperimentSummaryRead:
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/accept-result",
            json=payload.model_dump(mode="json"),
        )
        return ExperimentSummaryRead.model_validate(data)

    def reject_experiment_result(
        self,
        experiment_id: uuid.UUID,
        payload: ExperimentResultDecision,
    ) -> ExperimentSummaryRead:
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/reject-result",
            json=payload.model_dump(mode="json"),
        )
        return ExperimentSummaryRead.model_validate(data)

    # --- execution lock (CP-3) ---

    def acquire_experiment_lock(
        self, experiment_id: uuid.UUID, *, ttl_seconds: int
    ) -> ExperimentLockRead:
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/lock/acquire",
            json={"ttl_seconds": ttl_seconds},
        )
        return ExperimentLockRead.model_validate(data)

    def release_experiment_lock(self, experiment_id: uuid.UUID) -> ExperimentLockRead:
        data = self._json("POST", f"/experiments/{experiment_id}/lock/release")
        return ExperimentLockRead.model_validate(data)

    def force_release_experiment_lock(
        self,
        experiment_id: uuid.UUID,
        *,
        reason: str,
        actor: str | None = None,
    ) -> ExperimentLockRead:
        payload: dict[str, Any] = {"reason": reason}
        if actor:
            payload["actor"] = actor
        data = self._json("POST", f"/experiments/{experiment_id}/lock/force-release", json=payload)
        return ExperimentLockRead.model_validate(data)

    def record_experiment_lock_skip(
        self,
        experiment_id: uuid.UUID,
        *,
        next_attempt_at: str,
    ) -> ExperimentLockRead:
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/lock/skip",
            json={"next_attempt_at": next_attempt_at},
        )
        return ExperimentLockRead.model_validate(data)

    def scan_stalled_experiment_locks(self) -> ExperimentLockStalledScanRead:
        data = self._json("POST", "/experiments/lock/scan-stalled")
        return ExperimentLockStalledScanRead.model_validate(data)

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

    def list_reviews(
        self,
        experiment_id: uuid.UUID,
        *,
        include_archived: bool = False,
        plan_version: int | None = None,
    ) -> list[ReviewRead]:
        params: dict[str, Any] = {}
        if include_archived:
            params["include_archived"] = "true"
        if plan_version is not None:
            params["plan_version"] = str(plan_version)
        data = self._json(
            "GET",
            f"/experiments/{experiment_id}/reviews",
            params=params or None,
        )
        return [ReviewRead.model_validate(item) for item in data]

    def withdraw_review(self, experiment_id: uuid.UUID, review_id: uuid.UUID) -> None:
        self._json("POST", f"/experiments/{experiment_id}/reviews/{review_id}/withdraw")

    def update_review_item(self, item_id: uuid.UUID, status: ReviewItemStatus) -> ReviewItemRead:
        payload = ReviewItemUpdate(status=status)
        data = self._json(
            "PATCH",
            f"/review-items/{item_id}",
            json=payload.model_dump(mode="json"),
        )
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

    def create_log(
        self, experiment_id: uuid.UUID, payload: ExperimentLogCreate
    ) -> LogCreateResponse:
        data = self._json("POST", f"/experiments/{experiment_id}/logs", json=payload.model_dump())
        return LogCreateResponse.model_validate(data)

    def list_logs(self, experiment_id: uuid.UUID) -> list[ExperimentLogRead]:
        data = self._json("GET", f"/experiments/{experiment_id}/logs")
        return [ExperimentLogRead.model_validate(item) for item in data]

    # 0db51e10 I2(5e): record a cross-persona acceptance_status compare call.
    # Writes an audit_logs row with action="cross_persona_call" + the
    # per-persona visibility diff so R6 metrics / admin audit list can
    # aggregate later. Host persona only (the endpoint enforces
    # ensure_experiment_access).
    def record_cross_persona_call(
        self,
        experiment_id: uuid.UUID,
        *,
        visibility_diff: dict[str, Any] | None = None,
        result_partition_count: int = 0,
        diff_size: int = 0,
    ) -> AuditLogRead:
        payload = CrossPersonaCallRecord(
            visibility_diff=visibility_diff or {},
            result_partition_count=result_partition_count,
            diff_size=diff_size,
        )
        data = self._json(
            "POST",
            f"/experiments/{experiment_id}/cross-persona-call",
            json=payload.model_dump(mode="json"),
        )
        return AuditLogRead.model_validate(data)

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

    # --- todos ---

    def get_todos(self) -> TodoRead:
        return TodoRead.model_validate(self._json("GET", "/agents/me/todos"))

    def get_agent_work(
        self,
        *,
        notification_limit: int = 50,
        notification_category: NotificationCategory | str | None = "wakeable",
    ) -> AgentWorkRead:
        params: dict[str, Any] = {"notification_limit": notification_limit}
        if notification_category is None or notification_category == "all":
            params["notification_category"] = "all"
        elif isinstance(notification_category, NotificationCategory):
            params["notification_category"] = notification_category.value
        else:
            params["notification_category"] = notification_category
        return AgentWorkRead.model_validate(self._json("GET", "/agents/me/work", params=params))

    def get_agent_work_summary(
        self,
        *,
        include_all_personas: bool = False,
        topics_limit: int = 10,
        experiments_limit: int = 5,
    ) -> AgentWorkSummaryRead:
        params: dict[str, Any] = {
            "include_all_personas": str(include_all_personas).lower(),
            "topics_limit": topics_limit,
            "experiments_limit": experiments_limit,
        }
        return AgentWorkSummaryRead.model_validate(
            self._json("GET", "/agents/me/work/summary", params=params)
        )

    def get_topic_progress(self) -> TopicProgressListRead:
        return TopicProgressListRead.model_validate(self._json("GET", "/agents/me/topic-progress"))

    def dismiss_mention(self, mention_id: uuid.UUID) -> DismissMentionResultRead:
        data = self._json("POST", f"/agents/me/mentions/{mention_id}/dismiss")
        return DismissMentionResultRead.model_validate(data)

    def dismiss_all_mentions(self) -> DismissAllMentionsResultRead:
        data = self._json("POST", "/agents/me/mentions/dismiss-all")
        return DismissAllMentionsResultRead.model_validate(data)

    # --- notifications ---

    def list_notifications(
        self,
        *,
        unread_only: bool = False,
        category: NotificationCategory | str | None = None,
        target_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> NotificationListRead:
        params: dict[str, Any] = {"unread_only": unread_only, "limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category.value if isinstance(category, NotificationCategory) else category
        if target_type is not None:
            params["target_type"] = target_type
        data = self._json(
            "GET",
            "/agents/me/notifications",
            params=params,
        )
        return NotificationListRead.model_validate(data)

    def mark_notification_read(self, notification_id: uuid.UUID) -> NotificationRead:
        data = self._json("POST", f"/notifications/{notification_id}/read")
        return NotificationRead.model_validate(data)

    def mark_all_notifications_read(self) -> dict[str, int]:
        return self._json("POST", "/agents/me/notifications/read-all")

    # --- inbound events (runtime-waker dedup gate; D6) ---

    def record_inbound_event(
        self,
        payload: InboundEventCreate,
    ) -> InboundEventRecordResult:
        """Record that the caller is about to act on ``event_id``.

        Raises :class:`MAPHTTPError` with ``status_code == 409`` when the
        fingerprint already exists (server gate). Callers should treat 409 as
        "already woken" and skip the resume step (see plan D6 / A1 / A2).
        """
        data = self._json(
            "POST",
            "/agents/me/inbound-events",
            json=payload.model_dump(mode="json"),
        )
        return InboundEventRecordResult.model_validate(data)

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

    def list_audit_global(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        kind: str | None = None,
        experiment_id: uuid.UUID | None = None,
    ) -> tuple[list[AuditLogRead], int]:
        """List global audit entries with optional admin filters.

        ``kind`` matches ``AuditLog.action`` (e.g. ``"review_item.mutation"``).
        ``experiment_id`` filters to events whose ``payload_json`` carries
        the matching experiment id (used by ``map audit list --experiment <id>``).
        Both filters compose so the CLI can scope down to a single
        experiment × audit kind slice.
        """
        params: dict[str, str | int] = {"page": page, "page_size": page_size}
        if kind is not None:
            params["kind"] = kind
        if experiment_id is not None:
            params["experiment_id"] = str(experiment_id)
        resp = self._request("GET", "/admin/audit", params=params)
        items = [AuditLogRead.model_validate(a) for a in resp.json()]
        return items, int(resp.headers.get("X-Total-Count", len(items)))

    # --- status ---

    def get_global_status(self, project_id: uuid.UUID | None = None) -> GlobalStatusRead:
        params = {"project_id": str(project_id)} if project_id else None
        return GlobalStatusRead.model_validate(self._json("GET", "/status", params=params))

    # --- feedback ---

    def submit_feedback(self, payload: PlatformFeedbackCreate) -> PlatformFeedbackRead:
        data = self._json("POST", "/feedback", json=payload.model_dump(mode="json"))
        return PlatformFeedbackRead.model_validate(data)

    def list_feedback(
        self,
        *,
        status: FeedbackStatus | None = None,
        category: FeedbackCategory | None = None,
        project_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 50,
        include_archived: bool = False,
    ) -> list[PlatformFeedbackRead]:
        data, _total = self.list_feedback_page(
            status=status,
            category=category,
            project_id=project_id,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
        return data

    def list_feedback_page(
        self,
        *,
        status: FeedbackStatus | None = None,
        category: FeedbackCategory | None = None,
        project_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 50,
        include_archived: bool = False,
    ) -> tuple[list[PlatformFeedbackRead], int]:
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "include_archived": include_archived,
        }
        if status is not None:
            params["status"] = status.value
        if category is not None:
            params["category"] = category.value
        if project_id is not None:
            params["project_id"] = str(project_id)
        response = self._request("GET", "/feedback", params=params)
        data = response.json()
        return (
            [PlatformFeedbackRead.model_validate(item) for item in data],
            self._total_count(response),
        )

    def get_feedback(self, feedback_id: uuid.UUID) -> PlatformFeedbackRead:
        data = self._json("GET", f"/feedback/{feedback_id}")
        return PlatformFeedbackRead.model_validate(data)

    def update_feedback(
        self, feedback_id: uuid.UUID, payload: PlatformFeedbackUpdate
    ) -> PlatformFeedbackRead:
        data = self._json(
            "PATCH",
            f"/feedback/{feedback_id}",
            json=payload.model_dump(exclude_unset=True, mode="json"),
        )
        return PlatformFeedbackRead.model_validate(data)


# Re-export types useful for SDK consumers
__all__ = [
    "MAPClient",
    "CommentAnchorType",
    "ExperimentPhase",
    "ReviewItemStatus",
    "ExperimentCreate",
    "ExperimentComplete",
    "ExperimentResultDecision",
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
