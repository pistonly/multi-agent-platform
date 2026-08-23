import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from map_types.enums import NotificationCategory
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent, get_optional_current_agent
from server.db.session import get_db
from server.domain.models import Agent, AgentRole, Experiment, Project
from server.domain.schemas import (
    AgentCreate,
    AgentCreateResponse,
    AgentRead,
    AgentWorkRead,
    AgentWorkSummaryRead,
    DismissAllMentionsResultRead,
    DismissMentionResultRead,
    EscalationTargetRead,
    InboundEventCreate,
    InboundEventRead,
    InboundEventRecordResult,
    NotificationListRead,
    NotificationRead,
    TodoRead,
    TopicProgressListRead,
    TopicReadCursorRead,
)
from server.services import (
    agent_work_service,
    inbound_event_service,
    mention_service,
    notification_service,
    todo_service,
    topic_service,
)
from server.services import auth as auth_service
from server.services import permissions as perm
from server.services import project_service as svc
from server.services.errors import BadRequestError, ConflictError
from server.services.escalation_resolver import (
    escalation_label,
    resolve_escalation_target_with_tier,
)
from server.services.notification_stream import notification_sse_response

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
    payload: AgentCreate,
    db: Session = Depends(get_db),
    actor: Agent | None = Depends(get_optional_current_agent),
) -> AgentCreateResponse:
    perm.ensure_can_register_agent(db, actor, payload.role)

    existing = db.query(Agent).filter(Agent.name == payload.name).first()
    if existing:
        raise ConflictError("Agent name already exists")

    resolved_project_id: uuid.UUID | None = None
    if payload.role == AgentRole.agent:
        if payload.project_id is not None:
            resolved_project_id = svc.get_project(db, payload.project_id).id
        elif payload.project_key is not None:
            resolved_project_id = svc.get_project_by_key(db, payload.project_key).id
        else:
            raise BadRequestError(
                "project_id or project_key is required for role=agent"
            )

    try:
        agent, token = auth_service.create_agent(
            db, payload.name, payload.role, project_id=resolved_project_id
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
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


@agents_router.get("/me/escalation-target", response_model=EscalationTargetRead)
def get_my_escalation_target(
    experiment_id: uuid.UUID | None = Query(default=None),
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> EscalationTargetRead:
    """Resolve the escalation contact for a STATE_MACHINE.* error (I1(c)).

    The CLI calls this endpoint whenever it gets a ``MAPHTTPError`` with
    a ``STATE_MACHINE_*`` ``error_code`` so it can append an
    ``Escalation: @<name>`` line to the error output. The server runs
    the same 3-tier rule documented on ``experiments.escalation_target_agent_id``
    (override → caller → same-role → admin) and returns the chosen agent
    + the tier that picked it.

    When ``experiment_id`` is omitted, only the caller + role-based rules
    apply (no override available).
    """
    experiment = None
    if experiment_id is not None:
        experiment = db.get(Experiment, experiment_id)
        # Tolerate missing experiment — fall through to the role-based path
        # so the caller still gets a useful escalation contact.
    target_id, tier = resolve_escalation_target_with_tier(
        db,
        experiment=experiment,
        caller_agent_id=agent.id,
        project_id=agent.project_id,
    )
    label = escalation_label(db, escalation_target_id=target_id)
    return EscalationTargetRead(
        experiment_id=experiment_id,
        escalation_target_id=target_id,
        escalation_label=label,
        tier=tier,
    )


@agents_router.get("/me/todos", response_model=TodoRead)
def get_my_todos(
    include_all_partitions: bool = Query(default=False),
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> TodoRead:
    return todo_service.get_todos(
        db, agent, include_all_partitions=include_all_partitions
    )


@agents_router.get("/me/work", response_model=AgentWorkRead)
def get_my_work(
    notification_limit: int = Query(default=50, ge=1, le=200),
    notification_category: NotificationCategory | str | None = Query(default="wakeable"),
    client: str | None = Query(
        default=None,
        help="Caller identity hint. 'waker' marks a simple-waker poll (refreshes "
        "last_waker_poll_at); any call refreshes last_api_seen_at.",
    ),
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> AgentWorkRead:
    """Unified work snapshot: whoami + topic-progress + todos + unread notifications.

    Waker-heartbeat record point (D1): any call refreshes ``last_api_seen_at``;
    a waker-marked call (``?client=waker``) additionally refreshes
    ``last_waker_poll_at``. Both are single-column UPDATEs in this handler (not the
    ``get_current_agent`` middleware) so the waker's whoami+work double touch does
    not blur the semantics — stale detection reads only ``last_waker_poll_at``.
    """
    normalized_category: NotificationCategory | None
    if notification_category is None or notification_category == "all":
        normalized_category = None
    elif isinstance(notification_category, NotificationCategory):
        normalized_category = notification_category
    else:
        try:
            normalized_category = NotificationCategory(notification_category)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="notification_category must be wakeable, digest, or all",
            ) from exc
    now = datetime.now(timezone.utc)
    values: dict[str, datetime] = {"last_api_seen_at": now}
    if client == "waker":
        values["last_waker_poll_at"] = now
    db.execute(update(Agent).where(Agent.id == agent.id).values(**values))
    db.commit()
    return agent_work_service.get_agent_work(
        db,
        agent,
        notification_limit=notification_limit,
        notification_category=normalized_category,
    )


@agents_router.get("/me/work/summary", response_model=AgentWorkSummaryRead)
def get_my_work_summary(
    include_all_personas: bool = Query(default=False),
    topics_limit: int = Query(default=10, ge=1, le=100),
    experiments_limit: int = Query(default=5, ge=1, le=50),
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> AgentWorkSummaryRead:
    """6-bucket by_kind summary of the agent's work.

    Drives the /work top summary card and ``map work --summary``. Defaults
    filter host-only buckets for non-host personas; pass
    ``include_all_personas=true`` to opt in.
    """
    return agent_work_service.get_agent_work_summary(
        db,
        agent,
        include_all_personas=include_all_personas,
        topics_limit=topics_limit,
        experiments_limit=experiments_limit,
    )


@agents_router.get("/me/topic-progress", response_model=TopicProgressListRead)
def get_my_topic_progress(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> TopicProgressListRead:
    from server.services import topic_progress_service

    return topic_progress_service.list_topic_progress_for_agent(db, agent)


@agents_router.post(
    "/me/topics/{topic_id}/read",
    response_model=TopicReadCursorRead,
)
def mark_my_topic_read(
    topic_id: uuid.UUID,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> TopicReadCursorRead:
    return topic_service.mark_topic_read(db, agent=agent, topic_id=topic_id)


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
    category: NotificationCategory | str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> NotificationListRead:
    normalized_category: NotificationCategory | None
    if category is None or category == "all":
        normalized_category = None
    elif isinstance(category, NotificationCategory):
        normalized_category = category
    else:
        try:
            normalized_category = NotificationCategory(category)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="category must be wakeable, digest, or all",
            ) from exc
    items, total = notification_service.list_for_agent(
        db,
        agent,
        unread_only=unread_only,
        category=normalized_category,
        target_type=target_type,
        limit=limit,
        offset=offset,
    )
    unread_count = notification_service.count_unread(
        db,
        agent,
        category=normalized_category,
        target_type=target_type,
    )
    return NotificationListRead(
        items=[NotificationRead.model_validate(n) for n in items],
        total=total,
        unread_count=unread_count,
    )


@agents_router.get("/me/notifications/stream")
def stream_my_notifications(
    agent: Agent = Depends(get_current_agent),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    # Last-Event-ID 由浏览器 EventSource 在断线重连时自动发送，服务端从
    # per-agent ring buffer 中 replay id > last_event_id 的事件。
    return notification_sse_response(agent.id, last_event_id)


@agents_router.post("/me/notifications/read-all")
def mark_all_notifications_read(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    count = notification_service.mark_all_read(db, agent)
    return {"marked": count}


@agents_router.post(
    "/me/inbound-events",
    response_model=InboundEventRecordResult,
)
def record_my_inbound_event(
    payload: InboundEventCreate,
    response: Response,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> InboundEventRecordResult:
    """Record that the caller has seen ``payload.event_id`` and intends to act
    on it. This is the D6 server-side primary dedup gate: ``UNIQUE(fingerprint)``
    enforces A1 (replay rejection) and A2 (concurrent claim) across processes
    and restarts. A replay returns 409 Conflict — waker treats that as
    "already woken" and skips resume.

    v0.9 (M30A/M31 I2): legacy v1 fingerprints (``inbound:<event_id>``) return
    200 OK with ``status="rejected_v1"`` and the bumped ``rejection_count`` so
    the audit row is preserved but the waker is told to skip resume. We do NOT
    map this to 409 because v1 is a known-legacy shape (not a cross-process
    race) and a 409 would invite retry loops.
    """
    event, result_status = inbound_event_service.record_inbound_event(
        db, agent, payload
    )
    if result_status == "duplicate":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"inbound_event with fingerprint '{payload.fingerprint}' already exists; "
                "treating as already woken (D6 server gate)"
            ),
        )
    if result_status == "rejected_v1":
        # Legacy fingerprint: row persisted (or upserted) with rejection_count
        # bumped. Use 200 instead of 201 to signal "we accepted the record but
        # you should NOT proceed with the resume".
        response.status_code = status.HTTP_200_OK
        return InboundEventRecordResult(
            status="rejected_v1",
            event=InboundEventRead.model_validate(event),
        )
    response.status_code = status.HTTP_201_CREATED
    return InboundEventRecordResult(
        status="recorded",
        event=InboundEventRead.model_validate(event),
    )
