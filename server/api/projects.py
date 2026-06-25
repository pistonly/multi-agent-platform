import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit, http_error
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import (
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectStatusRevise,
    ProjectStatusVersionRead,
    ProjectUpdate,
)
from server.services import permissions as perm
from server.services import project_service as svc
from server.services import project_status_service as status_doc_service
from server.services.errors import ConflictError, ForbiddenError, NotFoundError

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    dependencies=[Depends(bind_background_tasks)],
)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        perm.require_admin(agent)
        project = svc.create_project(db, payload, author_agent_id=agent.id)
    except ForbiddenError as exc:
        raise http_error(exc) from exc
    except ConflictError as exc:
        raise http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
def list_projects(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ProjectRead]:
    if perm.is_admin(agent):
        projects = svc.list_projects(db, include_archived=include_archived)
    elif agent.project_id is None:
        projects = []
    else:
        projects = svc.list_projects(
            db, include_archived=include_archived, project_id=agent.project_id
        )
    return [ProjectRead.model_validate(p) for p in projects]


@router.get("/by-key/{project_key}", response_model=ProjectRead)
def get_project_by_key(
    project_key: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        project = svc.get_project_by_key(db, project_key)
        perm.ensure_project_access(agent, project.id)
    except NotFoundError as exc:
        raise http_error(exc) from exc
    except ForbiddenError as exc:
        raise http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        perm.ensure_project_access(agent, project_id)
        project = svc.get_project(db, project_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        perm.require_admin(agent)
        project = svc.update_project(db, project_id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("/{project_id}/status", response_model=ProjectStatusRead)
def get_project_status(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusRead:
    try:
        perm.ensure_project_access(agent, project_id)
        return svc.get_project_status(db, project_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc


@router.post(
    "/{project_id}/status/revisions",
    response_model=ProjectStatusVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def revise_project_status(
    project_id: uuid.UUID,
    payload: ProjectStatusRevise,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusVersionRead:
    try:
        perm.require_admin(agent)
        version = status_doc_service.revise_status(db, project_id, agent.id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc
    emit(
        db,
        agent,
        action="project_status.revised",
        target_type="project_status_version",
        target_id=version.id,
        project_id=project_id,
        summary=f"修订项目 Status v{version.version}",
    )
    return version


@router.get("/{project_id}/status/versions", response_model=list[ProjectStatusVersionRead])
def list_project_status_versions(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ProjectStatusVersionRead]:
    try:
        perm.ensure_project_access(agent, project_id)
        return status_doc_service.list_status_versions(db, project_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc


@router.get("/{project_id}/status/versions/{version}", response_model=ProjectStatusVersionRead)
def get_project_status_version(
    project_id: uuid.UUID,
    version: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusVersionRead:
    try:
        perm.ensure_project_access(agent, project_id)
        return status_doc_service.get_status_version(db, project_id, version)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc


