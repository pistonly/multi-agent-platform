"""MAP slimming: read local MD files by relative path.

``GET /projects/{project_id}/docs/read?path=docs/topics/foo/round1.md``
reads a Markdown file from the project's ``workspace_path`` and returns
its content. Path traversal is blocked by resolving the real path and
checking it stays within the workspace root.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, Project
from server.services import permissions as perm

docs_router = APIRouter(tags=["docs"])

_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB


class DocReadResponse(BaseModel):
    path: str
    content: str
    size: int
    exists: bool = True


@docs_router.get(
    "/projects/{project_id}/docs/read",
    response_model=DocReadResponse,
)
def read_doc(
    project_id: uuid.UUID,
    path: str = Query(..., description="Relative path within the project workspace"),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> DocReadResponse:
    resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
    perm.ensure_project_access(agent, resolved_project_id)

    project = db.get(Project, resolved_project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    workspace = Path(project.workspace_path).resolve()
    target = (workspace / path).resolve()

    try:
        target.relative_to(workspace)
    except ValueError as err:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Path is outside the project workspace",
        ) from err

    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File not found: {path}")

    if target.suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only .md, .markdown, .txt files are allowed",
        )

    size = target.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File too large ({size} bytes; max {_MAX_FILE_BYTES})",
        )

    content = target.read_text(encoding="utf-8")
    return DocReadResponse(path=path, content=content, size=size)
