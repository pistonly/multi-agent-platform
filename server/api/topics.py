import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, TopicStatus
from server.domain.schemas import (
    TopicAdvanceRound,
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
    TopicCreate,
    TopicDecisionRead,
    TopicRead,
    TopicResolve,
    TopicSummaryRead,
    TopicUpdate,
)
from server.services import notification_service, topic_service
from server.services import permissions as perm
from server.services.errors import ForbiddenError

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
    resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
    perm.ensure_project_access(agent, resolved_project_id)
    topic = topic_service.create_topic(db, resolved_project_id, agent.id, payload)
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
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicSummaryRead]:
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
        include_archived=include_archived,
        viewer_agent_id=agent.id,
    )
    response.headers["X-Total-Count"] = str(total)
    return topics


@topics_router.get("/topics/{topic_id}", response_model=TopicRead)
def get_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicRead:
    perm.ensure_topic_access(db, agent, topic_id)
    return topic_service.get_topic_detail(db, topic_id)


@topics_router.patch("/topics/{topic_id}", response_model=TopicSummaryRead)
def update_topic(
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    perm.ensure_topic_access(db, agent, topic_id)
    topic = topic_service.update_topic(db, topic_id, payload)
    return topic_service.topic_summary(db, topic)


@topics_router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    perm.ensure_topic_access(db, agent, topic_id)
    topic_service.soft_delete_topic(db, topic_id)


@topics_router.post("/topics/{topic_id}/close", response_model=TopicSummaryRead)
def close_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    perm.ensure_topic_access(db, agent, topic_id)
    topic = topic_service.set_topic_status(db, topic_id, TopicStatus.closed)
    # Phase 2 D2: kind-directed SSE so the waker can map to ``topic_lifecycle``.
    notification_service.emit_kind(
        db,
        project_id=topic.project_id,
        actor_id=agent.id,
        personas=["host", "participant"],
        event="topic.lifecycle.closed",
        summary=f"话题已关闭「{topic.title}」",
        target_type="topic",
        target_id=topic.id,
        payload={"topic_id": str(topic.id), "title": topic.title, "status": "closed"},
    )
    return topic_service.topic_summary(db, topic)


@topics_router.post("/topics/{topic_id}/dismiss", response_model=TopicSummaryRead)
def dismiss_my_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    """Host-only: hide an open topic from the creator's /todos.

    Auto re-surfaces when the topic gets new activity (e.g. new comments).
    """
    topic = topic_service.dismiss_topic(db, agent=agent, topic_id=topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic_service.topic_summary(db, topic)


@topics_router.post("/topics/{topic_id}/reopen", response_model=TopicSummaryRead)
def reopen_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    perm.ensure_topic_access(db, agent, topic_id)
    topic = topic_service.set_topic_status(db, topic_id, TopicStatus.open)
    # Phase 2 D2: kind-directed SSE for reopen so the waker can map to
    # ``topic_lifecycle`` and resume the participant wake loop.
    notification_service.emit_kind(
        db,
        project_id=topic.project_id,
        actor_id=agent.id,
        personas=["host", "participant"],
        event="topic.lifecycle.reopened",
        summary=f"话题已重开「{topic.title}」",
        target_type="topic",
        target_id=topic.id,
        payload={"topic_id": str(topic.id), "title": topic.title, "status": "open"},
    )
    return topic_service.topic_summary(db, topic)


@topics_router.post("/topics/{topic_id}/advance-round", response_model=TopicSummaryRead)
def advance_topic_round(
    topic_id: uuid.UUID,
    payload: TopicAdvanceRound | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    topic = perm.ensure_topic_access(db, agent, topic_id)
    body = payload or TopicAdvanceRound()

    if body.ack is not None:
        topic = topic_service.record_participant_round_ack(db, topic_id, agent, body.ack)
        return topic_service.topic_summary(db, topic)

    if topic.creator_agent_id != agent.id and not perm.is_admin(agent):
        raise ForbiddenError("Only the topic host or admin can advance the discussion round")
    topic = topic_service.advance_topic_round(
        db,
        topic_id,
        increment_summary=body.increment_summary,
        acknowledged_by=body.acknowledged_by,
    )
    emit(
        db,
        agent,
        action="topic.advance_round",
        target_type="topic",
        target_id=topic.id,
        project_id=topic.project_id,
        summary=f"推进话题轮次至 {topic.discussion_round.value}",
        event="topic.advance_round",
        event_payload={
            "topic_id": str(topic.id),
            "discussion_round": topic.discussion_round.value,
            "round_summary_count": topic.round_summary_count,
        },
    )
    return topic_service.topic_summary(db, topic)


@topics_router.post("/topics/{topic_id}/resolve", response_model=TopicDecisionRead)
def resolve_topic(
    topic_id: uuid.UUID,
    payload: TopicResolve,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicDecisionRead:
    topic = perm.ensure_topic_access(db, agent, topic_id)
    if topic.creator_agent_id != agent.id and not perm.is_admin(agent):
        raise ForbiddenError("Only the topic host or admin can resolve the topic")
    decision = topic_service.resolve_topic(db, topic_id, agent, payload)
    emit(
        db,
        agent,
        action="topic.resolved",
        target_type="topic",
        target_id=topic.id,
        project_id=topic.project_id,
        summary=f"沉淀话题结论「{topic.title}」",
        event="topic.resolved",
        event_payload={"topic_id": str(topic.id), "decision_id": str(decision.id)},
    )
    return topic_service.topic_decision_read(db, decision)


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
    topic = perm.ensure_topic_access(db, agent, topic_id)
    comment, unresolved = topic_service.create_topic_comment(db, topic_id, agent, payload)
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
    return topic_service.topic_comment_read(db, comment, unresolved_mentions=unresolved)


@topics_router.get("/topics/{topic_id}/comments")
def list_topic_comments(
    topic_id: uuid.UUID,
    tree: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
    perm.ensure_topic_access(db, agent, topic_id)
    return topic_service.list_topic_comments(db, topic_id, tree=tree)
