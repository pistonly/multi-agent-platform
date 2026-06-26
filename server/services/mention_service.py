import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Comment, Mention, MentionSourceType, Topic, TopicComment
from server.services import notification_service

MENTION_PATTERN = re.compile(r"@([a-zA-Z][a-zA-Z0-9_-]*)")
_EXCERPT_LEN = 200


def extract_mention_names(body: str) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for match in MENTION_PATTERN.finditer(body):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def resolve_mentioned_agents(db: Session, names: list[str]) -> list[Agent]:
    if not names:
        return []
    return list(db.scalars(select(Agent).where(Agent.name.in_(names))))


def _excerpt(body: str) -> str:
    text = body.strip().replace("\n", " ")
    if len(text) <= _EXCERPT_LEN:
        return text
    return text[: _EXCERPT_LEN - 1] + "…"


def process_experiment_comment_mentions(
    db: Session,
    *,
    comment: Comment,
    author: Agent,
    project_id: uuid.UUID,
    experiment_title: str,
) -> None:
    agents = resolve_mentioned_agents(db, extract_mention_names(comment.body))
    if not agents:
        return

    excerpt = _excerpt(comment.body)
    recipient_ids: list[uuid.UUID] = []
    for agent in agents:
        if agent.id == author.id:
            continue
        mention = Mention(
            mentioned_agent_id=agent.id,
            author_agent_id=author.id,
            source_type=MentionSourceType.experiment_comment,
            source_id=comment.id,
            project_id=project_id,
            experiment_id=comment.experiment_id,
            topic_id=None,
            excerpt=excerpt,
        )
        db.add(mention)
        recipient_ids.append(agent.id)

    if not recipient_ids:
        return

    db.commit()
    notification_service.enqueue_for_agents(
        db,
        recipient_agent_ids=recipient_ids,
        project_id=project_id,
        actor_id=author.id,
        event="agent.mentioned",
        summary=f"{author.name} 在实验「{experiment_title}」中提及了你",
        target_type="comment",
        target_id=comment.id,
        payload={
            "experiment_id": str(comment.experiment_id),
            "comment_id": str(comment.id),
            "author_name": author.name,
            "excerpt": excerpt,
        },
    )


def process_topic_comment_mentions(
    db: Session,
    *,
    comment: TopicComment,
    author: Agent,
    topic: Topic,
) -> None:
    agents = resolve_mentioned_agents(db, extract_mention_names(comment.body))
    if not agents:
        return

    excerpt = _excerpt(comment.body)
    recipient_ids: list[uuid.UUID] = []
    for agent in agents:
        if agent.id == author.id:
            continue
        mention = Mention(
            mentioned_agent_id=agent.id,
            author_agent_id=author.id,
            source_type=MentionSourceType.topic_comment,
            source_id=comment.id,
            project_id=topic.project_id,
            experiment_id=None,
            topic_id=topic.id,
            excerpt=excerpt,
        )
        db.add(mention)
        recipient_ids.append(agent.id)

    if not recipient_ids:
        return

    db.commit()
    notification_service.enqueue_for_agents(
        db,
        recipient_agent_ids=recipient_ids,
        project_id=topic.project_id,
        actor_id=author.id,
        event="agent.mentioned",
        summary=f"{author.name} 在话题「{topic.title}」中提及了你",
        target_type="topic_comment",
        target_id=comment.id,
        payload={
            "topic_id": str(topic.id),
            "comment_id": str(comment.id),
            "author_name": author.name,
            "excerpt": excerpt,
        },
    )


def list_mentions_for_agent(db: Session, agent_id: uuid.UUID, *, limit: int = 50) -> list[Mention]:
    return list(
        db.scalars(
            select(Mention)
            .where(Mention.mentioned_agent_id == agent_id)
            .order_by(Mention.created_at.desc())
            .limit(min(limit, 200))
        )
    )
