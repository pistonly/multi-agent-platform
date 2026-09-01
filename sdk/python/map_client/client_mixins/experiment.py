from __future__ import annotations

import uuid
from typing import Any

from map_types import (
    AuditLogRead,
    CommentCreate,
    CommentRead,
    CommentTreeNode,
    CrossPersonaCallRecord,
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
    LogCreateResponse,
    PlanRevise,
    PlanVersionRead,
    ReviewCreate,
    ReviewItemRead,
    ReviewItemStatus,
    ReviewItemUpdate,
    ReviewRead,
)


class ExperimentMixin:
    """experiments / phase / lock / plans / reviews / comments / logs 资源域方法。"""

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
