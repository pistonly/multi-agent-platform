"""Project / bootstrap / project-status schemas."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .base import ORMModel
from .experiment import ExperimentSummaryRead
from .topic import TopicSummaryRead

PROJECT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]{0,62}$")
CONTENT_ROOT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def normalize_content_root(value: str | None) -> str:
    """Single relative directory name; never a path."""
    root = (value or "").strip() or "map"
    if root in {".", ".."} or "/" in root or "\\" in root:
        raise ValueError("content_root must be a single directory name")
    if not CONTENT_ROOT_PATTERN.fullmatch(root):
        raise ValueError("content_root must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    return root


# --- Project ---


class ProjectCreate(BaseModel):
    project_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    workspace_path: str = Field(min_length=1, max_length=1024)
    description: str | None = None
    content_root: str = "map"
    fs_freshness_sla_seconds: int | None = Field(default=None, ge=1)

    @field_validator("project_key")
    @classmethod
    def validate_project_key(cls, value: str) -> str:
        key = value.strip().lower()
        if not PROJECT_KEY_PATTERN.match(key):
            raise ValueError("project_key must match [a-z0-9][a-z0-9-_]{0,62}")
        return key

    @field_validator("content_root")
    @classmethod
    def validate_content_root(cls, value: str) -> str:
        return normalize_content_root(value)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    workspace_path: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = None
    archived: bool | None = None
    content_root: str | None = None
    fs_freshness_sla_seconds: int | None = Field(default=None, ge=1)

    @field_validator("content_root")
    @classmethod
    def validate_content_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_content_root(value)


class ProjectRead(ORMModel):
    id: uuid.UUID
    project_key: str
    name: str
    workspace_path: str
    content_root: str = "map"
    fs_freshness_sla_seconds: int | None = None
    description: str | None
    current_status_version: int
    created_at: datetime
    archived_at: datetime | None


# --- Bootstrap (self-service) ---


class BootstrapRequest(BaseModel):
    """Body for ``POST /api/v1/bootstrap`` — self-service project + persona agents.

    Lets a new user create a project and the 3 default persona agents
    (host/participant/reviewer) in a single atomic call without an admin
    token. Returns the plaintext API tokens (shown once).
    """

    project_key: str = Field(min_length=2, max_length=64)
    project_name: str = Field(min_length=1, max_length=255)
    workspace_path: str = Field(min_length=1, max_length=1024)
    description: str | None = None
    content_root: str = "map"
    fs_freshness_sla_seconds: int | None = Field(default=None, ge=1)

    @field_validator("content_root")
    @classmethod
    def validate_content_root(cls, value: str) -> str:
        return normalize_content_root(value)


class BootstrapAgentResult(BaseModel):
    persona: str
    agent_id: uuid.UUID
    agent_name: str
    api_token: str


class BootstrapResponse(BaseModel):
    project: ProjectRead
    agents: list[BootstrapAgentResult]


# --- Token reissue (self-service, M52C) ---


class TokenReissueRequest(BaseModel):
    """Body for ``POST /api/v1/bootstrap/reissue`` — reissue one agent token.

    The endpoint requires a valid Bearer token: an admin, or an agent of
    the target project. ``project_key`` (committed in ``.map/config.yaml``)
    identifies the project but is NOT by itself proof of ownership — a leaked
    public key cannot take over a persona's token. Reissuing immediately
    revokes the previous token, so a lost ``.map/agents.local.yaml`` is
    recoverable as long as some same-project credential (or admin) remains.
    """

    project_key: str = Field(min_length=2, max_length=64)
    agent_name: str = Field(min_length=1, max_length=255)


class TokenReissueResponse(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    project_key: str
    api_token: str
    previous_token_revoked: bool = True
    reissued_at: datetime


class ProjectStatusRead(BaseModel):
    project: ProjectRead
    experiment_counts_by_phase: dict[str, int]
    active_experiments: list["ExperimentSummaryRead"] = Field(default_factory=list)
    recent_experiments: list["ExperimentSummaryRead"]
    open_topics: list["TopicSummaryRead"] = Field(default_factory=list)
    status_version: int = 0
    status_md: str | None = None
    status_updated_at: datetime | None = None


class ProjectStatusRevise(BaseModel):
    content_md: str = Field(min_length=1)
    change_note: str | None = Field(default=None, max_length=1024)


class ProjectStatusVersionRead(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    content_md: str
    author_agent_id: uuid.UUID
    change_note: str | None
    created_at: datetime


# --- Status ---


class WakerHeartbeatRead(BaseModel):
    """Per-agent waker liveness row, computed server-side for ``/status``.

    ``stale`` is only True for an agent that HAS heartbeated (last_waker_poll_at
    not null) AND not heartbeated within the threshold. null ``last_waker_poll_at``
    means ``never`` (no waker poll ever) and is deliberately NOT stale — the
    "only one waker" deployment keeps other personas from permanently WARN-ing.
    The CLI renders this row but never re-derives the flag (single source of truth
    is the server).
    """

    agent_id: uuid.UUID
    agent_name: str
    persona: str | None
    last_waker_poll_at: datetime | None
    stale: bool = False


class GlobalStatusRead(BaseModel):
    total_experiments_by_phase: dict[str, int]
    projects: list[ProjectStatusRead]
    recent_experiments: list[ExperimentSummaryRead]
    waker_heartbeats: list[WakerHeartbeatRead] = Field(default_factory=list)
