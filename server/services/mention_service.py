import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
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


def partition_mention_names(
    db: Session, names: list[str]
) -> tuple[list[Agent], list[str]]:
    """Split @tokens into resolved MAP agents and names with no matching Agent."""
    if not names:
        return [], []
    agents = resolve_mentioned_agents(db, names)
    resolved_names = {agent.name for agent in agents}
    unresolved = [name for name in names if name not in resolved_names]
    return agents, unresolved


def unresolved_mention_names(db: Session, body: str) -> list[str]:
    _, unresolved = partition_mention_names(db, extract_mention_names(body))
    return unresolved


def notify_unresolved_mentions(
    db: Session,
    *,
    author: Agent,
    project_id: uuid.UUID,
    unresolved: list[str],
    target_type: str,
    target_id: uuid.UUID,
    context_label: str,
    payload: dict[str, str],
) -> None:
    """Soft-fail feedback: comment is kept, author is told which @names did not match."""
    if not unresolved:
        return
    labels = ", ".join(f"@{name}" for name in unresolved)
    notification_service.enqueue_for_agents(
        db,
        recipient_agent_ids=[author.id],
        project_id=project_id,
        actor_id=author.id,
        event="mention.unresolved",
        summary=(
            f"评论中的 {labels} 未匹配到 MAP Agent（{context_label}）。"
            "请运行 `map persona list` 查看 agent_name，@ 时使用全名而非 persona 短名。"
        ),
        target_type=target_type,
        target_id=target_id,
        payload={
            **payload,
            "unresolved_mentions": unresolved,
            "hint": "Run `map persona list` and @ the exact agent_name field.",
        },
        exclude_actor=False,
    )


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
    commit: bool = True,
) -> list[str]:
    names = extract_mention_names(comment.body)
    agents, unresolved = partition_mention_names(db, names)
    if agents:
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

        if recipient_ids:
            if commit:
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

    if unresolved:
        notify_unresolved_mentions(
            db,
            author=author,
            project_id=project_id,
            unresolved=unresolved,
            target_type="comment",
            target_id=comment.id,
            context_label=f"实验「{experiment_title}」",
            payload={
                "experiment_id": str(comment.experiment_id),
                "comment_id": str(comment.id),
            },
        )
    return unresolved


def process_topic_comment_mentions(
    db: Session,
    *,
    comment: TopicComment,
    author: Agent,
    topic: Topic,
    commit: bool = True,
) -> list[str]:
    names = extract_mention_names(comment.body)
    agents, unresolved = partition_mention_names(db, names)
    if agents:
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

        if recipient_ids:
            if commit:
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

    if unresolved:
        notify_unresolved_mentions(
            db,
            author=author,
            project_id=topic.project_id,
            unresolved=unresolved,
            target_type="topic_comment",
            target_id=comment.id,
            context_label=f"话题「{topic.title}」",
            payload={
                "topic_id": str(topic.id),
                "comment_id": str(comment.id),
            },
        )
    return unresolved


def list_mentions_for_agent(
    db: Session,
    agent_id: uuid.UUID,
    *,
    limit: int = 50,
    include_dismissed: bool = False,
) -> list[Mention]:
    stmt = select(Mention).where(Mention.mentioned_agent_id == agent_id)
    if not include_dismissed:
        stmt = stmt.where(Mention.dismissed_at.is_(None))
    stmt = stmt.order_by(Mention.created_at.desc()).limit(min(limit, 200))
    return list(db.scalars(stmt))


def dismiss_mention(db: Session, *, agent: Agent, mention_id: uuid.UUID) -> Mention | None:
    """Mark a mention as dismissed for the mentioned agent. Idempotent."""
    mention = db.get(Mention, mention_id)
    if mention is None or mention.mentioned_agent_id != agent.id:
        return None
    if mention.dismissed_at is None:
        mention.dismissed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(mention)
    return mention


def dismiss_all_for_agent(db: Session, agent: Agent) -> int:
    """Dismiss every open mention addressed to this agent. Returns rows touched."""
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Mention)
        .where(
            Mention.mentioned_agent_id == agent.id,
            Mention.dismissed_at.is_(None),
        )
        .values(dismissed_at=now)
    )
    db.commit()
    return result.rowcount or 0


def _experiment_thread_comment_ids(
    db: Session, *, comment_id: uuid.UUID, experiment_id: uuid.UUID
) -> list[uuid.UUID]:
    """All comment ids in the same thread (same root via parent_comment_id) within one experiment."""
    rows = db.execute(
        select(Comment.id, Comment.parent_comment_id).where(
            Comment.experiment_id == experiment_id
        )
    ).all()
    if not rows:
        return []
    by_id: dict[uuid.UUID, uuid.UUID | None] = {row[0]: row[1] for row in rows}
    if comment_id not in by_id:
        return []
    cur = comment_id
    while by_id[cur] is not None:
        cur = by_id[cur]
        if cur not in by_id:
            return []
    root_id = cur
    return [cid for cid in by_id if _walk_root(cid, by_id) == root_id]


def _topic_thread_comment_ids(
    db: Session, *, comment_id: uuid.UUID, topic_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = db.execute(
        select(TopicComment.id, TopicComment.parent_comment_id).where(
            TopicComment.topic_id == topic_id
        )
    ).all()
    if not rows:
        return []
    by_id: dict[uuid.UUID, uuid.UUID | None] = {row[0]: row[1] for row in rows}
    if comment_id not in by_id:
        return []
    cur = comment_id
    while by_id[cur] is not None:
        cur = by_id[cur]
        if cur not in by_id:
            return []
    root_id = cur
    return [cid for cid in by_id if _walk_root(cid, by_id) == root_id]


def _walk_root(
    cid: uuid.UUID, by_id: dict[uuid.UUID, uuid.UUID | None]
) -> uuid.UUID:
    cur = cid
    seen: set[uuid.UUID] = set()
    while by_id.get(cur) is not None:
        if cur in seen:
            break
        seen.add(cur)
        cur = by_id[cur]  # type: ignore[assignment]
    return cur


def auto_dismiss_mentions_after_comment(
    db: Session,
    *,
    new_comment_author: Agent,
    experiment_id: uuid.UUID | None,
    topic_id: uuid.UUID | None,
    new_comment_id: uuid.UUID,
    commit: bool = True,
) -> int:
    """Auto-dismiss open mentions after an agent participates in a topic/experiment."""
    count = auto_dismiss_mentions_for_author_in_thread(
        db,
        new_comment_author=new_comment_author,
        experiment_id=experiment_id,
        topic_id=topic_id,
        new_comment_id=new_comment_id,
        commit=False,
    )
    count += dismiss_mentions_answered_by_participation(
        db,
        agent_id=new_comment_author.id,
        topic_id=topic_id,
        experiment_id=experiment_id,
        commit=False,
    )
    if commit:
        db.commit()
    return count


def dismiss_mentions_answered_by_participation(
    db: Session,
    *,
    agent_id: uuid.UUID,
    topic_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
    commit: bool = True,
) -> int:
    """Dismiss open mentions when the agent already replied per thread/container rules."""
    filters = [
        Mention.mentioned_agent_id == agent_id,
        Mention.dismissed_at.is_(None),
    ]
    if topic_id is not None:
        filters.extend(
            [
                Mention.topic_id == topic_id,
                Mention.source_type == MentionSourceType.topic_comment,
            ]
        )
    elif experiment_id is not None:
        filters.extend(
            [
                Mention.experiment_id == experiment_id,
                Mention.source_type == MentionSourceType.experiment_comment,
            ]
        )
    else:
        return 0

    open_mentions = list(db.scalars(select(Mention).where(*filters)))
    to_dismiss = [
        mention.id
        for mention in open_mentions
        if _agent_replied_after_mention(db, mention=mention, agent_id=agent_id)
    ]
    if not to_dismiss:
        return 0
    now = datetime.now(timezone.utc)
    db.execute(update(Mention).where(Mention.id.in_(to_dismiss)).values(dismissed_at=now))
    if commit:
        db.commit()
    return len(to_dismiss)


def auto_dismiss_mentions_in_container(
    db: Session,
    *,
    author: Agent,
    topic_id: uuid.UUID | None,
    experiment_id: uuid.UUID | None,
) -> int:
    """Dismiss all open mentions for ``author`` inside one topic or experiment.

    Once the mentioned agent posts any comment in the container, treat earlier
    @mentions there as seen — even if the reply started a new top-level thread.
    """
    now = datetime.now(timezone.utc)
    filters = [
        Mention.mentioned_agent_id == author.id,
        Mention.dismissed_at.is_(None),
    ]
    if topic_id is not None:
        filters.extend(
            [
                Mention.topic_id == topic_id,
                Mention.source_type == MentionSourceType.topic_comment,
            ]
        )
    elif experiment_id is not None:
        filters.extend(
            [
                Mention.experiment_id == experiment_id,
                Mention.source_type == MentionSourceType.experiment_comment,
            ]
        )
    else:
        return 0
    result = db.execute(update(Mention).where(*filters).values(dismissed_at=now))
    db.commit()
    return result.rowcount or 0


def _is_reply_in_thread_to(
    comment: TopicComment | Comment,
    source: TopicComment | Comment,
    by_id: dict[uuid.UUID, TopicComment | Comment],
) -> bool:
    """True when ``comment`` is a descendant reply to ``source`` in the same thread."""
    cur: TopicComment | Comment | None = comment
    while cur is not None and cur.parent_comment_id is not None:
        if cur.parent_comment_id == source.id:
            return True
        cur = by_id.get(cur.parent_comment_id)
    return False


def _agent_replied_after_mention(
    db: Session,
    *,
    mention: Mention,
    agent_id: uuid.UUID,
) -> bool:
    """True when ``agent_id`` posted in the same container after the mention source."""
    if mention.source_type == MentionSourceType.topic_comment and mention.topic_id is not None:
        comments = list(
            db.scalars(
                select(TopicComment)
                .where(TopicComment.topic_id == mention.topic_id)
                .order_by(TopicComment.created_at.asc(), TopicComment.id.asc())
            )
        )
    elif (
        mention.source_type == MentionSourceType.experiment_comment
        and mention.experiment_id is not None
    ):
        comments = list(
            db.scalars(
                select(Comment)
                .where(Comment.experiment_id == mention.experiment_id)
                .order_by(Comment.created_at.asc(), Comment.id.asc())
            )
        )
    else:
        return False

    by_id = {comment.id: comment for comment in comments}
    source = by_id.get(mention.source_id)
    if source is None:
        return False

    for comment in comments:
        if comment.author_agent_id != agent_id:
            continue
        if comment.id == mention.source_id:
            continue
        if comment.created_at > source.created_at:
            return True
        if comment.created_at < source.created_at:
            continue
        if _is_reply_in_thread_to(comment, source, by_id):
            return True
        if comment.id > source.id:
            return True
    return False


def agent_replied_after_mention(
    db: Session,
    *,
    mention: Mention,
    agent_id: uuid.UUID,
) -> bool:
    """Public wrapper: True when agent posted in container after the mention source."""
    return _agent_replied_after_mention(db, mention=mention, agent_id=agent_id)


def reconcile_mentions_after_participation(db: Session, agent_id: uuid.UUID) -> int:
    """Dismiss stale open mentions when the agent already replied in the container.

    Covers historical rows that pre-date container-level auto-dismiss, and any
    comments that slipped through without triggering a dismiss write.

    Only counts participation **after** the mention source comment in container order —
    prior comments in the same topic/experiment must not dismiss a newer @mention.
    """
    open_mentions = list(
        db.scalars(
            select(Mention).where(
                Mention.mentioned_agent_id == agent_id,
                Mention.dismissed_at.is_(None),
            )
        )
    )
    if not open_mentions:
        return 0

    now = datetime.now(timezone.utc)
    to_dismiss: list[uuid.UUID] = []
    for mention in open_mentions:
        if _agent_replied_after_mention(db, mention=mention, agent_id=agent_id):
            to_dismiss.append(mention.id)

    if not to_dismiss:
        return 0
    db.execute(
        update(Mention).where(Mention.id.in_(to_dismiss)).values(dismissed_at=now)
    )
    db.commit()
    return len(to_dismiss)


def auto_dismiss_mentions_for_author_in_thread(
    db: Session,
    *,
    new_comment_author: Agent,
    experiment_id: uuid.UUID | None,
    topic_id: uuid.UUID | None,
    new_comment_id: uuid.UUID,
    commit: bool = True,
) -> int:
    """When an agent posts a reply in a thread, any @mention rows pointing at him
    inside that thread (i.e. whose source_id is one of the thread comment ids)
    are dismissed — he has obviously seen them.
    """
    if experiment_id is not None:
        thread_ids = _experiment_thread_comment_ids(
            db, comment_id=new_comment_id, experiment_id=experiment_id
        )
        source_type = MentionSourceType.experiment_comment
    elif topic_id is not None:
        thread_ids = _topic_thread_comment_ids(
            db, comment_id=new_comment_id, topic_id=topic_id
        )
        source_type = MentionSourceType.topic_comment
    else:
        return 0
    if not thread_ids:
        return 0
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Mention)
        .where(
            Mention.mentioned_agent_id == new_comment_author.id,
            Mention.source_type == source_type,
            Mention.source_id.in_(thread_ids),
            Mention.dismissed_at.is_(None),
        )
        .values(dismissed_at=now)
    )
    if commit:
        db.commit()
    return result.rowcount or 0
