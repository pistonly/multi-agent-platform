import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit, http_error
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, TopicStatus
from server.domain.schemas import (
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
    TopicCreate,
    TopicRead,
    TopicSummaryRead,
    TopicUpdate,
)
from server.services import notification_service, topic_service
from server.services import permissions as perm
from server.services.errors import ForbiddenError, NotFoundError, StateTransitionError

topics_router = APIRouter(tags=["topics"], dependencies=[Depends(bind_background_tasks)])


@topics_router.post(
    "/projects/{project_id}/topics",
    response_model=TopicSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_topic(
    project_id: uuid.UUID,
    payload: TopicCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    try:
        resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
        perm.ensure_project_access(agent, resolved_project_id)
        topic = topic_service.create_topic(db, resolved_project_id, agent.id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc
    emit(
        db,
        agent,
        action="topic.created",
        target_type="topic",
        target_id=topic.id,
        project_id=resolved_project_id,
        summary=f"创建话题「{topic.title}」",
        event="topic.created",
        event_payload={"id": str(topic.id), "title": topic.title},
    )
    return topic_service.topic_summary(db, topic)


@topics_router.get("/projects/{project_id}/topics", response_model=list[TopicSummaryRead])
def list_topics(
    project_id: uuid.UUID,
    response: Response,
    topic_status: TopicStatus | None = Query(default=None, alias="status"),
    creator_agent_id: uuid.UUID | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicSummaryRead]:
    try:
        resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
        perm.ensure_project_access(agent, resolved_project_id)
        topics, total = topic_service.list_topics(
            db,
            resolved_project_id,
            status=topic_status,
            creator_agent_id=creator_agent_id,
            q=q,
            page=page,
            page_size=page_size,
        )
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc
    response.headers["X-Total-Count"] = str(total)
    return topics


@topics_router.get("/topics/{topic_id}", response_model=TopicRead)
def get_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicRead:
    try:
        perm.ensure_topic_access(db, agent, topic_id)
        return topic_service.get_topic_detail(db, topic_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc


@topics_router.patch("/topics/{topic_id}", response_model=TopicSummaryRead)
def update_topic(
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    try:
        perm.ensure_topic_access(db, agent, topic_id)
        topic = topic_service.update_topic(db, topic_id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc
    return topic_service.topic_summary(db, topic)


@topics_router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    try:
        perm.ensure_topic_access(db, agent, topic_id)
        topic_service.soft_delete_topic(db, topic_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc


@topics_router.post("/topics/{topic_id}/close", response_model=TopicSummaryRead)
def close_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    try:
        perm.ensure_topic_access(db, agent, topic_id)
        topic = topic_service.set_topic_status(db, topic_id, TopicStatus.closed)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise http_error(exc) from exc
    return topic_service.topic_summary(db, topic)


@topics_router.post("/topics/{topic_id}/reopen", response_model=TopicSummaryRead)
def reopen_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    try:
        perm.ensure_topic_access(db, agent, topic_id)
        topic = topic_service.set_topic_status(db, topic_id, TopicStatus.open)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise http_error(exc) from exc
    return topic_service.topic_summary(db, topic)


@topics_router.post(
    "/topics/{topic_id}/comments",
    response_model=TopicCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_topic_comment(
    topic_id: uuid.UUID,
    payload: TopicCommentCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicCommentRead:
    try:
        topic = perm.ensure_topic_access(db, agent, topic_id)
        comment = topic_service.create_topic_comment(db, topic_id, agent, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc
    event_payload = {"topic_id": str(topic_id), "comment_id": str(comment.id)}
    emit(
        db,
        agent,
        action="topic.comment.created",
        target_type="topic_comment",
        target_id=comment.id,
        project_id=topic.project_id,
        summary="话题新评论",
        event="topic.comment.created",
        event_payload=event_payload,
        notify=False,
    )
    notification_service.notify_topic_comment_created(
        db,
        project_id=topic.project_id,
        actor_id=agent.id,
        creator_agent_id=topic.creator_agent_id,
        target_id=comment.id,
        payload=event_payload,
    )
    return topic_service.topic_comment_read(db, comment)


@topics_router.get("/topics/{topic_id}/comments")
def list_topic_comments(
    topic_id: uuid.UUID,
    tree: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
    try:
        perm.ensure_topic_access(db, agent, topic_id)
        return topic_service.list_topic_comments(db, topic_id, tree=tree)
    except (NotFoundError, ForbiddenError) as exc:
        raise http_error(exc) from exc


