from __future__ import annotations

import uuid
from typing import Any, cast

from map_types import (
    AgentHeartbeatCreate,
    AgentHeartbeatResult,
    AgentWorkRead,
    AgentWorkSummaryRead,
    AuditLogRead,
    DismissAllMentionsResultRead,
    DismissMentionResultRead,
    GlobalStatusRead,
    InboundEventCreate,
    InboundEventRecordResult,
    NotificationCategory,
    NotificationListRead,
    NotificationRead,
    TodoRead,
    TopicProgressListRead,
    WebhookCreate,
    WebhookCreateResponse,
    WebhookDeliveryRead,
    WebhookRead,
)


class TodoNotificationMixin:
    """todos / mentions / notifications / inbound / heartbeat / webhooks / audit / status 资源域方法。"""

    # --- todos ---

    def get_todos(self) -> TodoRead:
        return TodoRead.model_validate(self._json("GET", "/agents/me/todos"))

    def get_agent_work(
        self,
        *,
        notification_limit: int = 50,
        notification_category: NotificationCategory | str | None = "wakeable",
        client: str | None = None,
    ) -> AgentWorkRead:
        params: dict[str, Any] = {"notification_limit": notification_limit}
        if notification_category is None or notification_category == "all":
            params["notification_category"] = "all"
        elif isinstance(notification_category, NotificationCategory):
            params["notification_category"] = notification_category.value
        else:
            params["notification_category"] = notification_category
        if client is not None:
            params["client"] = client
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
        return cast(dict[str, int], self._json("POST", "/agents/me/notifications/read-all"))

    def dispatch_notification(
        self,
        *,
        recipient_agent_id: uuid.UUID,
        event: str,
        summary: str,
        target_type: str = "experiment",
        target_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        wakeable: bool = True,
    ) -> NotificationRead:
        """Send an in-app notification to another agent in the same project.

        Used by host-orchestrated ``host invoke --timeout`` as the cancellation
        channel: when an invoke times out, the host dispatches a wakeable
        notification to the target persona instead of silently killing the
        orphaned session.

        Raises :class:`MAPHTTPError` for missing/mis-project recipient or a
        self-dispatch (400/403/404 shape described on the server endpoint).
        """
        body: dict[str, Any] = {
            "recipient_agent_id": str(recipient_agent_id),
            "event": event,
            "summary": summary,
            "target_type": target_type,
            "target_id": str(target_id) if target_id is not None else None,
            "payload": payload,
            "wakeable": wakeable,
        }
        data = self._json("POST", "/agents/me/notifications/dispatch", json=body)
        return NotificationRead.model_validate(data)

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

    # --- waker busy heartbeat (experiment b3ec2e4d I2 — A1 验收) ---

    def agent_heartbeat(
        self,
        payload: AgentHeartbeatCreate,
    ) -> AgentHeartbeatResult:
        """PATCH ``agents.last_busy_since`` for the caller.

        与 ``/me/work`` 的 ``last_waker_poll_at`` 刷新（migration 050 / D1）
        解耦——waker 在进入 runtime 调用（remind → claude 子进程）前调用，
        期间 busy 信号由本路径承载；结束后调 ``busy_since=None`` 清零。
        失败抛 :class:`MAPHTTPError` / :class:`MAPError`，由 waker 兜底
        （不阻塞 remind 主流程）。
        """
        data = self._json(
            "POST",
            "/agents/me/heartbeat",
            json=payload.model_dump(mode="json"),
        )
        return AgentHeartbeatResult.model_validate(data)

    # --- webhooks (admin) ---

    def create_webhook(self, payload: WebhookCreate) -> WebhookCreateResponse:
        data = self._json("POST", "/webhooks", json=payload.model_dump(mode="json"))
        return WebhookCreateResponse.model_validate(data)

    def list_webhooks(
        self,
        project_id: uuid.UUID | None = None,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[WebhookRead]:
        params: dict[str, str] = {}
        if project_id is not None:
            params["project_id"] = str(project_id)
        if page is not None:
            params["page"] = str(page)
        if page_size is not None:
            params["page_size"] = str(page_size)
        data = self._json("GET", "/webhooks", params=params or None)
        return [WebhookRead.model_validate(w) for w in data]

    def delete_webhook(self, webhook_id: uuid.UUID) -> None:
        self._request("DELETE", f"/webhooks/{webhook_id}")

    def list_webhook_deliveries(self, webhook_id: uuid.UUID) -> list[WebhookDeliveryRead]:
        data = self._json("GET", f"/webhooks/{webhook_id}/deliveries")
        return [WebhookDeliveryRead.model_validate(d) for d in data]

    # --- audit ---

    def list_audit_for_target(
        self,
        target_type: str,
        target_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[AuditLogRead]:
        data = self._json(
            "GET",
            "/audit",
            params={
                "target_type": target_type,
                "target_id": str(target_id),
                "limit": max(1, min(limit, 200)),
            },
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

    # --- feedback: removed in v0.15 M62 (dead-letter box teardown, see
    # topic v015-feedback-deprecation-design). CLI keeps an exit-2 guide stub;
    # SDK callers get a clean AttributeError as the standard breaking signal. ---
