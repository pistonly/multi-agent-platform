from __future__ import annotations

import uuid
from dataclasses import dataclass

from map_client.client import MAPClient
from map_mcp._utils import parse_uuid
from map_types import AgentRole


@dataclass(frozen=True)
class AgentContext:
    role: AgentRole
    project_id: uuid.UUID | None
    project_key: str | None

    @property
    def is_admin(self) -> bool:
        return self.role == AgentRole.admin

    @classmethod
    def from_client(cls, client: MAPClient) -> AgentContext:
        me = client.get_me()
        return cls(role=me.role, project_id=me.project_id, project_key=me.project_key)

    def resolve_project_id(self, project_id: str | None) -> uuid.UUID:
        if project_id:
            pid = parse_uuid(project_id, "project_id")
            if not self.is_admin and self.project_id and pid != self.project_id:
                raise ValueError("Access denied: project_id does not match bound project")
            return pid
        if self.project_id:
            return self.project_id
        raise ValueError("project_id is required for admin agents without a bound project")

    def require_admin(self) -> None:
        if not self.is_admin:
            raise ValueError("Admin role required for this tool")
