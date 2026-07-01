import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Comment, CommentAnchorType, PlanVersion, Review, ReviewItem
from server.domain.schemas import CommentCreate, CommentRead, CommentTreeNode
from server.services.errors import NotFoundError
from server.services.project_service import get_experiment


def _validate_anchor(db: Session, experiment_id: uuid.UUID, anchor_type: CommentAnchorType, anchor_id: uuid.UUID) -> None:
    if anchor_type == CommentAnchorType.plan:
        plan = db.scalar(
            select(PlanVersion).where(PlanVersion.id == anchor_id, PlanVersion.experiment_id == experiment_id)
        )
        if plan is None:
            raise NotFoundError("Plan anchor not found")
    elif anchor_type == CommentAnchorType.review:
        review = db.scalar(
            select(Review).where(Review.id == anchor_id, Review.experiment_id == experiment_id)
        )
        if review is None:
            raise NotFoundError("Review anchor not found")
    elif anchor_type == CommentAnchorType.review_item:
        item = db.scalar(select(ReviewItem).where(ReviewItem.id == anchor_id))
        if item is None or item.review.experiment_id != experiment_id:
            raise NotFoundError("Review item anchor not found")
    elif anchor_type == CommentAnchorType.comment:
        parent = db.scalar(
            select(Comment).where(Comment.id == anchor_id, Comment.experiment_id == experiment_id)
        )
        if parent is None:
            raise NotFoundError("Comment anchor not found")


def create_comment(
    db: Session,
    experiment_id: uuid.UUID,
    author: Agent,
    payload: CommentCreate,
) -> Comment:
    get_experiment(db, experiment_id)
    _validate_anchor(db, experiment_id, payload.anchor_type, payload.anchor_id)

    if payload.parent_id is not None:
        parent = db.scalar(
            select(Comment).where(Comment.id == payload.parent_id, Comment.experiment_id == experiment_id)
        )
        if parent is None:
            raise NotFoundError("Parent comment not found")

    comment = Comment(
        experiment_id=experiment_id,
        anchor_type=payload.anchor_type,
        anchor_id=payload.anchor_id,
        parent_comment_id=payload.parent_id,
        author_agent_id=author.id,
        body=payload.body,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    from server.services import mention_service

    experiment = get_experiment(db, experiment_id)
    mention_service.process_experiment_comment_mentions(
        db,
        comment=comment,
        author=author,
        project_id=experiment.project_id,
        experiment_title=experiment.title,
    )
    mention_service.auto_dismiss_mentions_after_comment(
        db,
        new_comment_author=author,
        experiment_id=experiment_id,
        topic_id=None,
        new_comment_id=comment.id,
    )
    return comment


def list_comments(db: Session, experiment_id: uuid.UUID) -> list[Comment]:
    get_experiment(db, experiment_id)
    stmt = select(Comment).where(Comment.experiment_id == experiment_id).order_by(Comment.created_at.asc())
    return list(db.scalars(stmt))


def _agent_names_by_ids(db: Session, agent_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not agent_ids:
        return {}
    return {
        agent.id: agent.name
        for agent in db.scalars(select(Agent).where(Agent.id.in_(agent_ids)))
    }


def comment_read(db: Session, comment: Comment) -> CommentRead:
    author_names = _agent_names_by_ids(db, {comment.author_agent_id})
    return CommentRead(
        id=comment.id,
        experiment_id=comment.experiment_id,
        anchor_type=comment.anchor_type,
        anchor_id=comment.anchor_id,
        parent_comment_id=comment.parent_comment_id,
        author_agent_id=comment.author_agent_id,
        author_name=author_names.get(comment.author_agent_id),
        body=comment.body,
        created_at=comment.created_at,
    )


def comments_to_read(db: Session, comments: list[Comment]) -> list[CommentRead]:
    author_names = _agent_names_by_ids(db, {comment.author_agent_id for comment in comments})
    return [
        CommentRead(
            id=comment.id,
            experiment_id=comment.experiment_id,
            anchor_type=comment.anchor_type,
            anchor_id=comment.anchor_id,
            parent_comment_id=comment.parent_comment_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
            body=comment.body,
            created_at=comment.created_at,
        )
        for comment in comments
    ]


def build_comment_tree(db: Session, comments: list[Comment]) -> list[CommentTreeNode]:
    author_names = _agent_names_by_ids(db, {comment.author_agent_id for comment in comments})
    nodes: dict[uuid.UUID, CommentTreeNode] = {}
    for comment in comments:
        nodes[comment.id] = CommentTreeNode(
            id=comment.id,
            experiment_id=comment.experiment_id,
            anchor_type=comment.anchor_type,
            anchor_id=comment.anchor_id,
            parent_comment_id=comment.parent_comment_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
            body=comment.body,
            created_at=comment.created_at,
            children=[],
        )

    roots: list[CommentTreeNode] = []
    for comment in comments:
        node = nodes[comment.id]
        if comment.parent_comment_id and comment.parent_comment_id in nodes:
            nodes[comment.parent_comment_id].children.append(node)
        else:
            roots.append(node)
    return roots
