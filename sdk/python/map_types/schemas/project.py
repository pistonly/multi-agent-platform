"""Project / bootstrap / project-status schemas."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .base import ORMModel
from .experiment import ExperimentSummaryRead
from .topic import TopicSummaryRead

PROJECT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]{0,62}$")


# --- Project ---


class ProjectCreate(BaseModel):
    project_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    workspace_path: str = Field(min_length=1, max_length=1024)
    description: str | None = None

    @field_validator("project_key")
    @classmethod
    def validate_project_key(cls, value: str) -> str:
        key = value.strip().lower()
        if not PROJECT_KEY_PATTERN.match(key):
            raise ValueError("project_key must match [a-z0-9][a-z0-9-_]{0,62}")
        return key


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    workspace_path: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = None
    archived: bool | None = None


class ProjectRead(ORMModel):
    id: uuid.UUID
    project_key: str
    name: str
    workspace_path: str
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


class BootstrapAgentResult(BaseModel):
    persona: str
    agent_id: uuid.UUID
    agent_name: str
    api_token: str


class BootstrapResponse(BaseModel):
    project: ProjectRead
    agents: list[BootstrapAgentResult]


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


class GlobalStatusRead(BaseModel):
    total_experiments_by_phase: dict[str, int]
    projects: list[ProjectStatusRead]
    recent_experiments: list[ExperimentSummaryRead]
