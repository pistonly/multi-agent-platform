import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent, get_optional_current_agent
from server.db.session import get_db
from server.domain.models import Agent, AgentRole, Project
from server.domain.schemas import (
    AgentCreateResponse,
    AgentRead,
    DismissAllMentionsResultRead,
    DismissMentionResultRead,
    NotificationListRead,
    NotificationRead,
    TodoRead,
)
from server.services import auth as auth_service
from server.services import mention_service
from server.services import notification_service
from server.services import todo_service
from server.services.notification_stream import notification_sse_response
from server.services import permissions as perm
from server.services import project_service as svc

agents_router = APIRouter(prefix="/agents", tags=["agents"])


@agents_router.get("", response_model=list[AgentRead])
def list_agents(
    role: AgentRole | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[AgentRead]:
    """List agents visible to the caller.

    - Admins see every agent (with optional filters).
    - Project-bound agents see admins and agents within their own project.
    """
    stmt = select(Agent).order_by(Agent.created_at.asc())
    if not perm.is_admin(agent):
        stmt = stmt.where(
            (Agent.role == AgentRole.admin) | (Agent.project_id == agent.project_id)
        )
    if role is not None:
        stmt = stmt.where(Agent.role == role)
    if project_id is not None:
        stmt = stmt.where(Agent.project_id == project_id)

    agents = list(db.scalars(stmt))
    project_keys: dict[uuid.UUID, str] = {}
    project_ids = {a.project_id for a in agents if a.project_id is not None}
    if project_ids:
        rows = db.scalars(select(Project).where(Project.id.in_(project_ids))).all()
        project_keys = {row.id: row.project_key for row in rows}

    return [
        AgentRead(
            id=a.id,
            name=a.name,
            role=a.role,
            project_id=a.project_id,
            project_key=project_keys.get(a.project_id) if a.project_id else None,
            created_at=a.created_at,
        )
        for a in agents
    ]


@agents_router.post("", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
def register_agent(
    name: str,
    role: AgentRole = AgentRole.agent,
    project_id: uuid.UUID | None = Query(default=None),
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: Agent | None = Depends(get_optional_current_agent),
) -> AgentCreateResponse:
    perm.ensure_can_register_agent(db, actor, role)

    existing = db.query(Agent).filter(Agent.name == name).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Agent name already exists")

    resolved_project_id: uuid.UUID | None = None
    if role == AgentRole.agent:
        if project_id is not None:
            resolved_project_id = svc.get_project(db, project_id).id
        elif project_key is not None:
            resolved_project_id = svc.get_project_by_key(db, project_key).id
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


@agents_router.post(
    "/me/mentions/{mention_id}/dismiss",
    response_model=DismissMentionResultRead,
)
def dismiss_my_mention(
    mention_id: uuid.UUID,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> DismissMentionResultRead:
    mention = mention_service.dismiss_mention(db, agent=agent, mention_id=mention_id)
    if mention is None:
        raise HTTPException(status_code=404, detail="Mention not found")
    assert mention.dismissed_at is not None  # for type checker
    return DismissMentionResultRead(id=mention.id, dismissed_at=mention.dismissed_at)


@agents_router.post(
    "/me/mentions/dismiss-all",
    response_model=DismissAllMentionsResultRead,
)
def dismiss_all_my_mentions(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> DismissAllMentionsResultRead:
    count = mention_service.dismiss_all_for_agent(db, agent)
    return DismissAllMentionsResultRead(dismissed=count)


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


