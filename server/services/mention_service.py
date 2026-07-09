import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Comment, Mention, MentionSourceType, Topic, TopicComment
from server.services import notification_service, thread_activity
from server.services.text_utils import excerpt as _excerpt

MENTION_PATTERN = re.compile(r"@([a-zA-Z][a-zA-Z0-9_-]*)")


def _iter_text_outside_markdown_code(body: str):
    """Yield substrings of *body* that lie outside inline/fenced Markdown code."""
    i = 0
    n = len(body)
    while i < n:
        if body.startswith("```", i) or body.startswith("~~~", i):
            fence = body[i : i + 3]
            i += 3
            while i < n and body[i] != "\n":
                i += 1
            if i < n:
                i += 1
            while i < n:
                if body.startswith(fence, i):
                    i += 3
                    break
                i += 1
            continue
        if body[i] == "`":
            j = i
            while j < n and body[j] == "`":
                j += 1
            tick_count = j - i
            i = j
            while i < n:
                if body[i] == "`":
                    k = i
                    while k < n and body[k] == "`":
                        k += 1
                    if k - i >= tick_count:
                        i = k
                        break
                i += 1
            continue
        start = i
        while (
            i < n
            and body[i] != "`"
            and not body.startswith("```", i)
            and not body.startswith("~~~", i)
        ):
            i += 1
        if start < i:
            yield body[start:i]


def extract_mention_names(body: str) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for segment in _iter_text_outside_markdown_code(body):
        for match in MENTION_PATTERN.finditer(segment):
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
    commit: bool = True,
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
        commit=commit,
    )


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
                commit=commit,
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
            commit=commit,
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
                commit=commit,
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
            commit=commit,
        )
    return unresolved


def process_topic_mentions(
    db: Session,
    *,
    topic: Topic,
    author: Agent,
    commit: bool = True,
) -> list[str]:
    """Create mention todos/notifications from a topic's initial description."""
    body = topic.description or ""
    names = extract_mention_names(body)
    agents, unresolved = partition_mention_names(db, names)
    if agents:
        excerpt = _excerpt(body)
        recipient_ids: list[uuid.UUID] = []
        for agent in agents:
            if agent.id == author.id:
                continue
            mention = Mention(
                mentioned_agent_id=agent.id,
                author_agent_id=author.id,
                source_type=MentionSourceType.topic,
                source_id=topic.id,
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
                target_type="topic",
                target_id=topic.id,
                payload={
                    "topic_id": str(topic.id),
                    "author_name": author.name,
                    "excerpt": excerpt,
                },
                commit=commit,
            )

    if unresolved:
        notify_unresolved_mentions(
            db,
            author=author,
            project_id=topic.project_id,
            unresolved=unresolved,
            target_type="topic",
            target_id=topic.id,
            context_label=f"话题「{topic.title}」",
            payload={"topic_id": str(topic.id)},
            commit=commit,
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


def _apply_mention_dismiss_cascade(
    db: Session,
    *,
    actor_agent_id: uuid.UUID,
    mentions: list[Mention],
    now: datetime | None = None,
    audit_action: str = "mention.dismiss",
    audit_payload_extra: dict | None = None,
) -> int:
    """Dismiss mentions, cascade-read matching notifications, write audit rows."""
    from server.services import audit_service, notification_service

    if not mentions:
        return 0
    now = now or datetime.now(UTC)
    touched: list[Mention] = []
    for mention in mentions:
        if mention.dismissed_at is not None:
            continue
        mention.dismissed_at = now
        touched.append(mention)
        payload = {
            "mention_id": str(mention.id),
            "source_comment_id": str(mention.source_id),
            "actor_agent_id": str(actor_agent_id),
        }
        if audit_payload_extra:
            payload.update(audit_payload_extra)
        audit_service.log_no_commit(
            db,
            action=audit_action,
            target_type="mention",
            agent_id=actor_agent_id,
            project_id=mention.project_id,
            target_id=mention.id,
            summary="Mention dismissed",
            payload=payload,
        )
    if not touched:
        return 0
    notification_service.mark_agent_mentioned_notifications_read_no_commit(
        db, mentions=touched, now=now
    )
    return len(touched)


def dismiss_mention(db: Session, *, agent: Agent, mention_id: uuid.UUID) -> Mention | None:
    """Mark a mention as dismissed for the mentioned agent. Idempotent."""
    mention = db.get(Mention, mention_id)
    if mention is None or mention.mentioned_agent_id != agent.id:
        return None
    if mention.dismissed_at is None:
        _apply_mention_dismiss_cascade(db, actor_agent_id=agent.id, mentions=[mention])
        db.commit()
        db.refresh(mention)
    return mention


def dismiss_all_for_agent(db: Session, agent: Agent) -> int:
    """Dismiss every open mention addressed to this agent. Returns rows touched."""
    mentions = list(
        db.scalars(
            select(Mention).where(
                Mention.mentioned_agent_id == agent.id,
                Mention.dismissed_at.is_(None),
            )
        )
    )
    count = _apply_mention_dismiss_cascade(db, actor_agent_id=agent.id, mentions=mentions)
    if count:
        db.commit()
    return count


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
    comment_body: str | None = None,
    commit: bool = True,
) -> int:
    """Auto-dismiss open mentions after an agent participates in a topic/experiment."""
    if topic_id is not None:
        from server.services import topic_ack_service

        body = comment_body
        if body is None:
            comment = db.get(TopicComment, new_comment_id)
            body = comment.body if comment is not None else None
        if body is not None and topic_ack_service._ack_kind(body) is not None:
            # Round-ack dismiss uses mention.auto_dismissed in record_participant_round_ack.
            return 0

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
                Mention.source_type.in_(
                    [MentionSourceType.topic, MentionSourceType.topic_comment]
                ),
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
    to_dismiss_objs = [
        mention
        for mention in open_mentions
        if thread_activity.comment_after(db, mention=mention, agent_id=agent_id)
    ]
    if not to_dismiss_objs:
        return 0
    count = _apply_mention_dismiss_cascade(
        db, actor_agent_id=agent_id, mentions=to_dismiss_objs
    )
    if commit and count:
        db.commit()
    return count


def _agent_replied_after_mention(
    db: Session,
    *,
    mention: Mention,
    agent_id: uuid.UUID,
) -> bool:
    return thread_activity.comment_after(db, mention=mention, agent_id=agent_id)


def agent_replied_after_mention(
    db: Session,
    *,
    mention: Mention,
    agent_id: uuid.UUID,
) -> bool:
    """Public wrapper: True when agent posted in container after the mention source."""
    return thread_activity.replied_after(db, mention=mention, agent_id=agent_id)


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
    mentions = list(
        db.scalars(
            select(Mention).where(
                Mention.mentioned_agent_id == new_comment_author.id,
                Mention.source_type == source_type,
                Mention.source_id.in_(thread_ids),
                Mention.dismissed_at.is_(None),
            )
        )
    )
    count = _apply_mention_dismiss_cascade(
        db, actor_agent_id=new_comment_author.id, mentions=mentions
    )
    if topic_id is not None:
        topic_mentions = list(
            db.scalars(
                select(Mention).where(
                    Mention.mentioned_agent_id == new_comment_author.id,
                    Mention.source_type == MentionSourceType.topic,
                    Mention.topic_id == topic_id,
                    Mention.dismissed_at.is_(None),
                )
            )
        )
        count += _apply_mention_dismiss_cascade(
            db, actor_agent_id=new_comment_author.id, mentions=topic_mentions
        )
    if commit and count:
        db.commit()
    return count


def auto_dismiss_mentions_for_round_ack(
    db: Session,
    *,
    topic: Topic,
    agent: Agent,
    ack_kind: str,
    commit: bool = True,
) -> int:
    """Dismiss open mentions in the latest Round Summary subtree after accept/dismiss ack."""
    import logging

    from server.services import topic_ack_service

    if ack_kind == "reject":
        return 0
    if ack_kind not in {"accept", "dismiss"}:
        return 0

    comments = topic_ack_service._topic_comments(db, topic.id)
    summary = topic_ack_service.latest_host_round_summary_comment(
        comments, host_agent_id=topic.creator_agent_id
    )
    if summary is None:
        logging.getLogger(__name__).debug(
            "round_ack auto_dismiss skip topic=%s agent=%s reason=no_summary",
            topic.id,
            agent.id,
        )
        return 0

    thread_ids = _topic_thread_comment_ids(
        db, comment_id=summary.id, topic_id=topic.id
    )
    if not thread_ids:
        return 0

    mentions = list(
        db.scalars(
            select(Mention).where(
                Mention.topic_id == topic.id,
                Mention.mentioned_agent_id == agent.id,
                Mention.source_type == MentionSourceType.topic_comment,
                Mention.source_id.in_(thread_ids),
                Mention.dismissed_at.is_(None),
            )
        )
    )
    if not mentions:
        logging.getLogger(__name__).debug(
            "round_ack auto_dismiss skip topic=%s agent=%s reason=no_open_mentions",
            topic.id,
            agent.id,
        )
        return 0

    audit_extra = {
        "triggered_by": f"round_ack:{ack_kind}",
        "summary_comment_id": str(summary.id),
        "dismissing_agent_id": str(agent.id),
        "round_id": topic.discussion_round.value,
    }
    count = _apply_mention_dismiss_cascade(
        db,
        actor_agent_id=agent.id,
        mentions=mentions,
        audit_action="mention.auto_dismissed",
        audit_payload_extra=audit_extra,
    )
    if commit and count:
        db.commit()
    return count
