import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from server.api.common import http_error
from server.api.deps import get_current_agent, get_optional_current_agent
from server.db.session import get_db
from server.domain.models import Agent, AgentRole
from server.domain.schemas import (
    AgentCreateResponse,
    AgentRead,
    NotificationListRead,
    NotificationRead,
    TodoRead,
)
from server.services import auth as auth_service
from server.services import notification_service
from server.services import todo_service
from server.services.notification_stream import notification_sse_response
from server.services import permissions as perm
from server.services import project_service as svc
from server.services.errors import ForbiddenError, NotFoundError, UnauthorizedError

agents_router = APIRouter(prefix="/agents", tags=["agents"])


@agents_router.post("", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
def register_agent(
    name: str,
    role: AgentRole = AgentRole.agent,
    project_id: uuid.UUID | None = Query(default=None),
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: Agent | None = Depends(get_optional_current_agent),
) -> AgentCreateResponse:
    try:
        perm.ensure_can_register_agent(db, actor, role)
    except (UnauthorizedError, ForbiddenError) as exc:
        raise http_error(exc) from exc

    existing = db.query(Agent).filter(Agent.name == name).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Agent name already exists")

    resolved_project_id: uuid.UUID | None = None
    if role == AgentRole.agent:
        if project_id is not None:
            try:
                resolved_project_id = svc.get_project(db, project_id).id
            except NotFoundError as exc:
                raise http_error(exc) from exc
        elif project_key is not None:
            try:
                resolved_project_id = svc.get_project_by_key(db, project_key).id
            except NotFoundError as exc:
                raise http_error(exc) from exc
        else:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="project_id or project_key is required for role=agent",
            )

    try:
        agent, token = auth_service.create_agent(db, name, role, project_id=resolved_project_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AgentCreateResponse(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        project_id=agent.project_id,
        created_at=agent.created_at,
        api_token=token,
    )


@agents_router.get("/me", response_model=AgentRead)
def get_me(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> AgentRead:
    project_key: str | None = None
    if agent.project_id is not None:
        project = svc.get_project(db, agent.project_id)
        project_key = project.project_key
    return AgentRead(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        project_id=agent.project_id,
        project_key=project_key,
        created_at=agent.created_at,
    )


@agents_router.get("/me/todos", response_model=TodoRead)
def get_my_todos(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> TodoRead:
    return todo_service.get_todos(db, agent)


@agents_router.get("/me/notifications", response_model=NotificationListRead)
def list_my_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> NotificationListRead:
    items, total = notification_service.list_for_agent(
        db, agent, unread_only=unread_only, limit=limit, offset=offset
    )
    unread_count = notification_service.count_unread(db, agent)
    return NotificationListRead(
        items=[NotificationRead.model_validate(n) for n in items],
        total=total,
        unread_count=unread_count,
    )


@agents_router.get("/me/notifications/stream")
def stream_my_notifications(agent: Agent = Depends(get_current_agent)) -> StreamingResponse:
    return notification_sse_response(agent.id)


@agents_router.post("/me/notifications/read-all")
def mark_all_notifications_read(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    count = notification_service.mark_all_read(db, agent)
    return {"marked": count}


