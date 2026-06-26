import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, Topic, TopicComment, TopicStatus
from server.domain.schemas import (
    ExperimentSummaryRead,
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
    TopicCreate,
    TopicRead,
    TopicSummaryRead,
    TopicUpdate,
)
from server.services.errors import NotFoundError, StateTransitionError
from server.services.project_service import get_project


def _get_topic(db: Session, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or topic.deleted_at is not None:
        raise NotFoundError("Topic not found")
    return topic


def topic_summary(db: Session, topic: Topic) -> TopicSummaryRead:
    return topic_summaries_for_topics(db, [topic])[0]


def topic_summaries_for_topics(db: Session, topics: list[Topic]) -> list[TopicSummaryRead]:
    if not topics:
        return []

    topic_ids = [topic.id for topic in topics]
    comment_counts = {
        topic_id: count
        for topic_id, count in db.execute(
            select(TopicComment.topic_id, func.count())
            .where(TopicComment.topic_id.in_(topic_ids))
            .group_by(TopicComment.topic_id)
        )
    }
    experiment_counts = {
        topic_id: count
        for topic_id, count in db.execute(
            select(Experiment.topic_id, func.count())
            .where(Experiment.topic_id.in_(topic_ids), Experiment.deleted_at.is_(None))
            .group_by(Experiment.topic_id)
        )
    }
    return [
        TopicSummaryRead(
            id=topic.id,
            project_id=topic.project_id,
            creator_agent_id=topic.creator_agent_id,
            title=topic.title,
            description=topic.description,
            status=topic.status,
            pinned=topic.pinned,
            comment_count=comment_counts.get(topic.id, 0),
            experiment_count=experiment_counts.get(topic.id, 0),
            created_at=topic.created_at,
            updated_at=topic.updated_at,
        )
        for topic in topics
    ]


def create_topic(
    db: Session,
    project_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    payload: TopicCreate,
) -> Topic:
    get_project(db, project_id)
    topic = Topic(
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        title=payload.title,
        description=payload.description,
        status=TopicStatus.open,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def list_topics(
    db: Session,
    project_id: uuid.UUID,
    *,
    status: TopicStatus | None = None,
    creator_agent_id: uuid.UUID | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[TopicSummaryRead], int]:
    get_project(db, project_id)
    stmt = select(Topic).where(Topic.project_id == project_id, Topic.deleted_at.is_(None))
    if status is not None:
        stmt = stmt.where(Topic.status == status)
    if creator_agent_id is not None:
        stmt = stmt.where(Topic.creator_agent_id == creator_agent_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Topic.title.ilike(pattern) | Topic.description.ilike(pattern))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    stmt = stmt.order_by(Topic.pinned.desc(), Topic.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    topics = list(db.scalars(stmt))
    return topic_summaries_for_topics(db, topics), total


def get_topic_detail(db: Session, topic_id: uuid.UUID) -> TopicRead:
    topic = _get_topic(db, topic_id)
    summary = topic_summary(db, topic)

    exp_stmt = (
        select(Experiment)
        .where(Experiment.topic_id == topic.id, Experiment.deleted_at.is_(None))
        .order_by(Experiment.created_at.desc())
    )
    experiments = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(exp_stmt)]
    comments = _build_comment_tree(list(db.scalars(
        select(TopicComment)
        .where(TopicComment.topic_id == topic.id)
        .order_by(TopicComment.created_at.asc())
    )))

    return TopicRead(**summary.model_dump(), experiments=experiments, comments=comments)


def update_topic(db: Session, topic_id: uuid.UUID, payload: TopicUpdate) -> Topic:
    topic = _get_topic(db, topic_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(topic, key, value)
    db.commit()
    db.refresh(topic)
    return topic


def soft_delete_topic(db: Session, topic_id: uuid.UUID) -> None:
    topic = _get_topic(db, topic_id)
    topic.deleted_at = datetime.now(UTC)
    db.commit()


def set_topic_status(db: Session, topic_id: uuid.UUID, target: TopicStatus) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status == target:
        return topic
    valid = (
        (target == TopicStatus.closed and topic.status == TopicStatus.open)
        or (target == TopicStatus.open and topic.status == TopicStatus.closed)
    )
    if not valid:
        raise StateTransitionError(f"Topic cannot move from {topic.status.value} to {target.value}")
    topic.status = target
    db.commit()
    db.refresh(topic)
    return topic


def _build_comment_tree(comments: list[TopicComment]) -> list[TopicCommentTreeNode]:
    nodes: dict[uuid.UUID, TopicCommentTreeNode] = {}
    for comment in comments:
        nodes[comment.id] = TopicCommentTreeNode(
            id=comment.id,
            topic_id=comment.topic_id,
            author_agent_id=comment.author_agent_id,
            parent_comment_id=comment.parent_comment_id,
            body=comment.body,
            created_at=comment.created_at,
            children=[],
        )
    roots: list[TopicCommentTreeNode] = []
    for comment in comments:
        node = nodes[comment.id]
        if comment.parent_comment_id and comment.parent_comment_id in nodes:
            nodes[comment.parent_comment_id].children.append(node)
        else:
            roots.append(node)
    return roots


def create_topic_comment(
    db: Session,
    topic_id: uuid.UUID,
    author: Agent,
    payload: TopicCommentCreate,
) -> TopicComment:
    topic = _get_topic(db, topic_id)
    if payload.parent_id is not None:
        parent = db.scalar(
            select(TopicComment).where(
                TopicComment.id == payload.parent_id, TopicComment.topic_id == topic_id
            )
        )
        if parent is None:
            raise NotFoundError("Parent comment not found")
    comment = TopicComment(
        topic_id=topic_id,
        author_agent_id=author.id,
        parent_comment_id=payload.parent_id,
        body=payload.body,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    from server.services import mention_service

    mention_service.process_topic_comment_mentions(db, comment=comment, author=author, topic=topic)
    return comment


def list_topic_comments(
    db: Session,
    topic_id: uuid.UUID,
    *,
    tree: bool = False,
) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
    _get_topic(db, topic_id)
    stmt = (
        select(TopicComment)
        .where(TopicComment.topic_id == topic_id)
        .order_by(TopicComment.created_at.asc())
    )
    comments = list(db.scalars(stmt))
    if tree:
        return _build_comment_tree(comments)
    return [TopicCommentRead.model_validate(c) for c in comments]
