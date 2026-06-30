import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from map_types.enums import AgentRole, ExperimentPhase, TopicActionItemStatus, TopicDiscussionRound
from server.domain.models import Agent, Experiment, Topic, TopicActionItem, TopicComment, TopicDecision, TopicStatus
from server.domain.schemas import (
    ExperimentSummaryRead,
    TopicActionItemRead,
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
from server.services.errors import ConflictError, NotFoundError, StateTransitionError
from server.services.project_service import get_project


def _get_topic(db: Session, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or topic.deleted_at is not None:
        raise NotFoundError("Topic not found")
    return topic


def _agent_names_by_ids(db: Session, agent_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not agent_ids:
        return {}
    return {
        agent.id: agent.name
        for agent in db.scalars(select(Agent).where(Agent.id.in_(agent_ids)))
    }


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
            .where(
                Experiment.topic_id.in_(topic_ids),
                Experiment.deleted_at.is_(None),
                Experiment.phase != ExperimentPhase.cancelled,
            )
            .group_by(Experiment.topic_id)
        )
    }
    creator_names = _agent_names_by_ids(db, {topic.creator_agent_id for topic in topics})
    return [
        TopicSummaryRead(
            id=topic.id,
            project_id=topic.project_id,
            creator_agent_id=topic.creator_agent_id,
            creator_name=creator_names.get(topic.creator_agent_id),
            title=topic.title,
            description=topic.description,
            status=topic.status,
            pinned=topic.pinned,
            discussion_round=topic.discussion_round,
            round_summary_count=topic.round_summary_count,
            comment_count=comment_counts.get(topic.id, 0),
            experiment_count=experiment_counts.get(topic.id, 0),
            created_at=topic.created_at,
            updated_at=topic.updated_at,
            archived_at=topic.archived_at,
        )
        for topic in topics
    ]


def _action_item_read(db: Session, item: TopicActionItem) -> TopicActionItemRead:
    owner_name = None
    if item.owner_agent_id is not None:
        owner = getattr(item, "owner", None)
        if owner is None:
            owner = db.get(Agent, item.owner_agent_id)
        owner_name = owner.name if owner else None
    return TopicActionItemRead(
        id=item.id,
        decision_id=item.decision_id,
        project_id=item.project_id,
        topic_id=item.topic_id,
        title=item.title,
        description=item.description,
        owner_agent_id=item.owner_agent_id,
        owner_name=owner_name,
        status=item.status,
        due_at=item.due_at,
        linked_experiment_id=item.linked_experiment_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def topic_decision_read(db: Session, decision: TopicDecision) -> TopicDecisionRead:
    author_name = None
    author = getattr(decision, "author", None)
    if author is None:
        author = db.get(Agent, decision.author_agent_id)
    if author is not None:
        author_name = author.name

    topic_title = None
    topic = getattr(decision, "topic", None)
    if topic is None:
        topic = db.get(Topic, decision.topic_id)
    if topic is not None:
        topic_title = topic.title

    return TopicDecisionRead(
        id=decision.id,
        project_id=decision.project_id,
        topic_id=decision.topic_id,
        topic_title=topic_title,
        author_agent_id=decision.author_agent_id,
        author_name=author_name,
        decision=decision.decision,
        rationale=decision.rationale,
        rejected_options=decision.rejected_options,
        open_questions=decision.open_questions,
        no_decision_reason=decision.no_decision_reason,
        action_items=[_action_item_read(db, item) for item in decision.action_items],
        created_at=decision.created_at,
        updated_at=decision.updated_at,
    )


def _load_decision(db: Session, topic_id: uuid.UUID) -> TopicDecision | None:
    stmt = (
        select(TopicDecision)
        .where(TopicDecision.topic_id == topic_id)
        .options(
            joinedload(TopicDecision.author),
            joinedload(TopicDecision.topic),
            joinedload(TopicDecision.action_items).joinedload(TopicActionItem.owner),
        )
    )
    return db.execute(stmt).unique().scalar_one_or_none()


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
    include_archived: bool = False,
) -> tuple[list[TopicSummaryRead], int]:
    get_project(db, project_id)
    stmt = select(Topic).where(Topic.project_id == project_id, Topic.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Topic.archived_at.is_(None))
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
        .where(
            Experiment.topic_id == topic.id,
            Experiment.deleted_at.is_(None),
            Experiment.phase != ExperimentPhase.cancelled,
        )
        .order_by(Experiment.created_at.desc())
    )
    experiments = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(exp_stmt)]
    comments = list(db.scalars(
        select(TopicComment)
        .where(TopicComment.topic_id == topic.id)
        .order_by(TopicComment.created_at.asc())
    ))
    comments_tree = _build_comment_tree(comments, _agent_names_by_ids(
        db, {comment.author_agent_id for comment in comments}
    ))

    decision = _load_decision(db, topic.id)
    return TopicRead(
        **summary.model_dump(),
        experiments=experiments,
        comments=comments_tree,
        decision=topic_decision_read(db, decision) if decision is not None else None,
    )


def resolve_topic(
    db: Session,
    topic_id: uuid.UUID,
    author: Agent,
    payload: TopicResolve,
) -> TopicDecision:
    topic = _get_topic(db, topic_id)
    decision = db.scalar(select(TopicDecision).where(TopicDecision.topic_id == topic_id))
    if decision is None:
        decision = TopicDecision(
            project_id=topic.project_id,
            topic_id=topic.id,
            author_agent_id=author.id,
        )
        db.add(decision)
        db.flush()
    else:
        decision.author_agent_id = author.id

    decision.decision = payload.decision
    decision.rationale = payload.rationale
    decision.rejected_options = payload.rejected_options
    decision.open_questions = payload.open_questions
    decision.no_decision_reason = payload.no_decision_reason

    db.execute(delete(TopicActionItem).where(TopicActionItem.decision_id == decision.id))
    for item_payload in payload.action_items:
        if item_payload.owner_agent_id is not None:
            owner = db.get(Agent, item_payload.owner_agent_id)
            if owner is None or (owner.project_id != topic.project_id and owner.role != AgentRole.admin):
                raise NotFoundError("Action item owner agent not found")
        if item_payload.linked_experiment_id is not None:
            experiment = db.get(Experiment, item_payload.linked_experiment_id)
            if experiment is None or experiment.deleted_at is not None or experiment.project_id != topic.project_id:
                raise NotFoundError("Linked experiment not found")
        db.add(
            TopicActionItem(
                decision_id=decision.id,
                project_id=topic.project_id,
                topic_id=topic.id,
                title=item_payload.title,
                description=item_payload.description,
                owner_agent_id=item_payload.owner_agent_id,
                status=TopicActionItemStatus.open,
                due_at=item_payload.due_at,
                linked_experiment_id=item_payload.linked_experiment_id,
            )
        )

    db.commit()
    loaded = _load_decision(db, topic.id)
    if loaded is None:
        raise NotFoundError("Topic decision not found")
    return loaded


def list_project_decisions(
    db: Session,
    project_id: uuid.UUID,
    *,
    limit: int = 20,
) -> list[TopicDecisionRead]:
    get_project(db, project_id)
    rows = list(
        db.scalars(
            select(TopicDecision)
            .where(TopicDecision.project_id == project_id)
            .options(
                joinedload(TopicDecision.author),
                joinedload(TopicDecision.topic),
                joinedload(TopicDecision.action_items).joinedload(TopicActionItem.owner),
            )
            .order_by(TopicDecision.updated_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        .unique()
    )
    return [topic_decision_read(db, row) for row in rows]


def list_action_items(
    db: Session,
    project_id: uuid.UUID,
    *,
    owner_agent_id: uuid.UUID | None = None,
    status: TopicActionItemStatus | None = None,
    limit: int = 100,
) -> list[TopicActionItemRead]:
    get_project(db, project_id)
    stmt = (
        select(TopicActionItem)
        .where(TopicActionItem.project_id == project_id)
        .options(joinedload(TopicActionItem.owner))
        .order_by(TopicActionItem.updated_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if owner_agent_id is not None:
        stmt = stmt.where(TopicActionItem.owner_agent_id == owner_agent_id)
    if status is not None:
        stmt = stmt.where(TopicActionItem.status == status)
    return [_action_item_read(db, item) for item in db.scalars(stmt)]


def update_topic(db: Session, topic_id: uuid.UUID, payload: TopicUpdate) -> Topic:
    topic = _get_topic(db, topic_id)
    data = payload.model_dump(exclude_unset=True)
    archived = data.pop("archived", None)
    for key, value in data.items():
        setattr(topic, key, value)
    if archived is not None:
        topic.archived_at = datetime.now(UTC) if archived else None
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


def advance_topic_round(db: Session, topic_id: uuid.UUID, *, increment_summary: bool = True) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status != TopicStatus.open:
        raise ConflictError("Cannot advance a closed topic")
    if topic.discussion_round == TopicDiscussionRound.ready:
        raise ConflictError("Topic discussion round is already ready")

    current_count = topic.round_summary_count or 0
    next_count = current_count + 1 if increment_summary else current_count

    if topic.discussion_round == TopicDiscussionRound.round1:
        if next_count < 1:
            raise ConflictError("Cannot advance round1 before at least one round summary")
        topic.discussion_round = TopicDiscussionRound.round2
    elif topic.discussion_round == TopicDiscussionRound.round2:
        if next_count < 2:
            raise ConflictError("Cannot advance round2 before two round summaries")
        topic.discussion_round = TopicDiscussionRound.ready
    else:
        raise ConflictError(f"Unknown topic discussion round: {topic.discussion_round}")

    topic.round_summary_count = next_count
    db.commit()
    db.refresh(topic)
    return topic


def _build_comment_tree(
    comments: list[TopicComment],
    author_names: dict[uuid.UUID, str],
) -> list[TopicCommentTreeNode]:
    nodes: dict[uuid.UUID, TopicCommentTreeNode] = {}
    for comment in comments:
        nodes[comment.id] = TopicCommentTreeNode(
            id=comment.id,
            topic_id=comment.topic_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
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
    mention_service.auto_dismiss_mentions_for_author_in_thread(
        db,
        new_comment_author=author,
        experiment_id=None,
        topic_id=topic_id,
        new_comment_id=comment.id,
    )
    return comment


def topic_comment_read(db: Session, comment: TopicComment) -> TopicCommentRead:
    author_names = _agent_names_by_ids(db, {comment.author_agent_id})
    return TopicCommentRead(
        id=comment.id,
        topic_id=comment.topic_id,
        author_agent_id=comment.author_agent_id,
        author_name=author_names.get(comment.author_agent_id),
        parent_comment_id=comment.parent_comment_id,
        body=comment.body,
        created_at=comment.created_at,
    )


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
    author_names = _agent_names_by_ids(db, {comment.author_agent_id for comment in comments})
    if tree:
        return _build_comment_tree(comments, author_names)
    return [
        TopicCommentRead(
            id=comment.id,
            topic_id=comment.topic_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
            parent_comment_id=comment.parent_comment_id,
            body=comment.body,
            created_at=comment.created_at,
        )
        for comment in comments
    ]
