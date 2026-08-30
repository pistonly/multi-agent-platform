"""Agent registry schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from map_types.enums import AgentRole

from .base import ORMModel


class AgentRead(ORMModel):
    id: uuid.UUID
    name: str
    role: AgentRole
    project_id: uuid.UUID | None
    project_key: str | None = None
    created_at: datetime


class AgentCreateResponse(AgentRead):
    api_token: str


class AgentCreate(BaseModel):
    """Body for ``POST /api/v1/agents`` (cleanup experiment f12a5638 P2 #5).

    Replaces the legacy query-param creation contract so the endpoint
    follows the same body-driven pattern as every other write endpoint
    in the API. ``project_id`` and ``project_key`` are both accepted for
    caller convenience — at most one must resolve to a project for
    ``role=agent`` (admin-only). When both are omitted the service layer
    raises ``ValueError`` which maps to 400.
    """

    name: str = Field(min_length=1, max_length=128)
    role: AgentRole = AgentRole.agent
    project_id: uuid.UUID | None = None
    project_key: str | None = None


# --- STATE_MACHINE.* escalation contact (experiment 156172e9 I1(c)) ---


class EscalationTargetRead(ORMModel):
    """Resolution of a STATE_MACHINE.* error's escalation contact.

    Returned by ``GET /api/v1/agents/me/escalation-target?experiment_id=...``.
    The CLI uses this on ``MAPHTTPError`` with a STATE_MACHINE.* error_code
    so the user knows who to ping about a state-machine refusal.
    """

    experiment_id: uuid.UUID | None
    escalation_target_id: uuid.UUID | None
    escalation_label: str  # "@name" or "<no escalation contact>" placeholder
    tier: str  # "experiment_override" | "current_caller" | "same_role_active" | "admin" | "none"


# --- waker busy heartbeat (experiment b3ec2e4d I1 — A1 验收) ---


class AgentHeartbeatCreate(BaseModel):
    """Body for ``POST /api/v1/agents/me/heartbeat``.

    Waker-side PATCH：写入 ``agents.last_busy_since``。``busy_since=None``
    视为 idle 清零。endpoint 单列 UPDATE（与既有 ``/me/work`` 的
    ``last_waker_poll_at`` 刷新路径解耦——busy_since 是 I2 waker state
    machine 的扩展信号，刷新时机由 waker 决定，不是 polling 副作用）。
    """

    busy_since: datetime | None = None


class AgentHeartbeatResult(BaseModel):
    """Return shape for ``POST /api/v1/agents/me/heartbeat``.

    Echo 写入后的状态便于 waker 端校验 round-trip。``busy_since=None``
    表示 idle 清零。
    """

    agent_id: uuid.UUID
    busy_since: datetime | None
