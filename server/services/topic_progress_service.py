"""Per-agent unread topic activity for open discussions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from server.domain.models import Agent
from server.domain.schemas import TopicProgressItemRead, TopicProgressListRead
from server.services import topic_work_item_service as work_items


def _with_fs_items(
    db: Session,
    agent: Agent,
    items: list[TopicProgressItemRead],
) -> list[TopicProgressItemRead]:
    """把 FS（``map/`` 目录）投影的待办并进 DB 结果，按 ``topic_id`` 去重。

    两个事实源：FS 话题的待办（缺 round 文件 / open action item / stale）只存在
    于 ``fs_source_service`` 的投影里，DB 侧查不到。此前只有 ``/agents/me/work``
    合并了这一路，``/me/topic-progress`` 漏接（FS 话题恒为 ``items: []``），
    A2A 端点则手工重复了一遍同样的合并——单源收敛在此处，调用方一律拿到完整
    结果，不再各自拼装。

    去重按 ``topic_id``：远程模式下投影缓存行可能与 DB 行指向同一话题，此时保留
    DB 结果（DB 侧信息更全：``new_comments`` / 逐 agent 读光标等）。
    """
    from server.services import fs_source_service

    fs_items = fs_source_service.fs_topic_progress_for_agent(db, agent)
    if not fs_items:
        return items
    merged = list(items)
    seen = {i.topic_id for i in merged}
    for item in fs_items:
        if item.topic_id in seen:
            continue
        merged.append(item)
        seen.add(item.topic_id)
    return merged


def list_topic_progress_for_agent(
    db: Session,
    agent: Agent,
    *,
    bundle: work_items.AgentTopicWorkItems | None = None,
    include_fs: bool = True,
) -> TopicProgressListRead:
    """List open topics with work items for ``agent`` (unified diff view).

    默认同时返回 DB 与 FS 两个来源；``include_fs=False`` 只要 DB 来源（留给
    需要纯 DB 视图的调用与「修复前行为」对照测试）。
    """
    if agent.project_id is None:
        return TopicProgressListRead(items=[], total=0)

    if bundle is None:
        bundle = work_items.topic_work_items_bundle_for_agent(db, agent)
    all_items = bundle.items
    items: list[TopicProgressItemRead] = []
    for topic in bundle.open_topics:
        topic_items = [i for i in all_items if i.topic_id == topic.id]
        if not topic_items:
            continue
        comments = bundle.comments_by_topic.get(topic.id)
        item = work_items.topic_progress_item_from_work_items(
            db,
            topic,
            agent,
            topic_items,
            comments=comments,
        )
        if item is not None:
            items.append(item)
    if include_fs:
        items = _with_fs_items(db, agent, items)
    return TopicProgressListRead(items=items, total=len(items))
