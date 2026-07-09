import uuid

from fastapi import APIRouter, Depends, Query, status
from map_types.enums import TopicActionItemStatus
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, AgentRole
from server.domain.schemas import (
    AgentRead,
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectStatusRevise,
    ProjectStatusVersionRead,
    ProjectUpdate,
    TopicActionItemRead,
    TopicDecisionRead,
)
from server.services import permissions as perm
from server.services import project_service as svc
from server.services import project_status_service as status_doc_service

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
    perm.require_admin(agent)
    project = svc.create_project(db, payload, author_agent_id=agent.id)
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
    project = svc.get_project_by_key(db, project_key)
    perm.ensure_project_access(agent, project.id)
    return ProjectRead.model_validate(project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    perm.ensure_project_access(agent, project_id)
    project = svc.get_project(db, project_id)
    return ProjectRead.model_validate(project)


@router.get("/{project_id}/agents", response_model=list[AgentRead])
def list_project_agents(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[AgentRead]:
    perm.ensure_project_access(agent, project_id)
    svc.get_project(db, project_id)
    agents = list(
        db.scalars(
            select(Agent)
            .where(or_(Agent.project_id == project_id, Agent.role == AgentRole.admin))
            .order_by(Agent.name)
        )
    )
    return [
        AgentRead(
            id=a.id,
            name=a.name,
            role=a.role,
            project_id=a.project_id,
            project_key=None,
            created_at=a.created_at,
        )
        for a in agents
    ]


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    perm.require_admin(agent)
    project = svc.update_project(db, project_id, payload)
    return ProjectRead.model_validate(project)


@router.get("/{project_id}/status", response_model=ProjectStatusRead)
def get_project_status(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusRead:
    perm.ensure_project_access(agent, project_id)
    return svc.get_project_status(db, project_id)


@router.get("/{project_id}/decisions", response_model=list[TopicDecisionRead])
def list_project_decisions(
    project_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicDecisionRead]:
    perm.ensure_project_access(agent, project_id)
    return svc.list_project_decisions(db, project_id, limit=limit)


@router.get("/{project_id}/action-items", response_model=list[TopicActionItemRead])
def list_project_action_items(
    project_id: uuid.UUID,
    owner_agent_id: uuid.UUID | None = Query(default=None),
    item_status: TopicActionItemStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicActionItemRead]:
    perm.ensure_project_access(agent, project_id)
    return svc.list_project_action_items(
        db,
        project_id,
        owner_agent_id=owner_agent_id,
        status=item_status,
        limit=limit,
    )


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
    perm.ensure_can_revise_project_status(agent, project_id)
    version = status_doc_service.revise_status(db, project_id, agent.id, payload)
    emit(
        db,
        agent,
        action="project_status.revised",
        target_type="project_status_version",
        target_id=version.id,
        project_id=project_id,
        summary=f"修订项目 Status v{version.version}",
    )
    return ProjectStatusVersionRead.model_validate(version)


@router.get("/{project_id}/status/versions", response_model=list[ProjectStatusVersionRead])
def list_project_status_versions(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ProjectStatusVersionRead]:
    perm.ensure_project_access(agent, project_id)
    return status_doc_service.list_status_versions(db, project_id, limit=limit)


@router.get("/{project_id}/status/versions/{version}", response_model=ProjectStatusVersionRead)
def get_project_status_version(
    project_id: uuid.UUID,
    version: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusVersionRead:
    perm.ensure_project_access(agent, project_id)
    return status_doc_service.get_status_version(db, project_id, version)
