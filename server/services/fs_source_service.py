"""map/ 文件夹事实源：实时解析桥接层。

职责边界：

- **读**：实时解析 ``<workspace>/<content_root>/``，产出 FS plane；并转换为
  DB 兼容的 ``TopicSummaryRead`` / ``TopicRead``，供主读路径（/topics）合并。
- **验证型写**：advance-round / close 在服务端校验（host 权限 + ack 完整性）
  后**写回 index.md**——平台不存内容，只改事实源文件的 front-matter。
- persona 名字 → Agent 的映射仅用于填充 DB 兼容 schema 的 agent id 字段
  （名字查不到时用 uuid5 合成稳定 id），不参与权限判断；FS 写权限只认
  creator 名字或 admin。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from map_fs import (
    FsPlane,
    FsTopic,
    FsWorkItem,
    derive_work,
    scan_plane,
    update_topic_index,
)
from map_types.enums import TopicCommentKind, TopicStatus
from map_types.schemas.fs import (
    FsCommentRead,
    FsExperimentRead,
    FsTopicDetailRead,
    FsTopicSummaryRead,
    FsWorkItemRead,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.domain.models import Agent, Project
from server.domain.schemas import (
    TopicCommentTreeNode,
    TopicProgressItemRead,
    TopicRead,
    TopicSummaryRead,
    TopicWorkItemRead,
)
from server.services.errors import ForbiddenError
from server.services.notification_service import PERSONA_AGENT_NAMES

_PERSONA_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs-persona")


class FsTopicNotFoundError(Exception):
    """话题文件夹不存在。"""


class FsAckPendingError(Exception):
    """本轮还有参与者未发言（ack 未满）。"""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"round ack pending: {', '.join(missing)}")
        self.missing = missing


class FsStateError(Exception):
    """话题当前状态不允许该操作（如已关闭再推进）。"""


# ---------------------------------------------------------------------------
# 基础：workspace / plane
# ---------------------------------------------------------------------------


def content_root_name() -> str:
    return get_settings().content_root


def plane_for_project(project: Project) -> FsPlane:
    return scan_plane(Path(project.workspace_path), content_root_name())


def fs_topic_or_raise(project: Project, slug: str) -> FsTopic:
    plane = plane_for_project(project)
    topic = plane.topic_by_slug(slug)
    if topic is None:
        raise FsTopicNotFoundError(f"fs topic not found: {slug}")
    return topic


# ---------------------------------------------------------------------------
# 读：FS plane schema
# ---------------------------------------------------------------------------


def fs_topic_summary(topic: FsTopic) -> FsTopicSummaryRead:
    return FsTopicSummaryRead(
        id=topic.id,
        slug=topic.slug,
        title=topic.title,
        description=topic.description,
        status=topic.status,
        discussion_round=topic.round,
        creator=topic.creator,
        comment_count=len(topic.comments),
        participants=topic.participants,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        dir_path=topic.dir_path,
    )


def fs_topic_detail(topic: FsTopic) -> FsTopicDetailRead:
    return FsTopicDetailRead(
        **fs_topic_summary(topic).model_dump(),
        comments=[
            FsCommentRead(
                id=c.id,
                topic_slug=c.topic_slug,
                round=c.round,
                author=c.author,
                kind=c.kind,
                is_round_summary=c.is_round_summary,
                excerpt=c.excerpt,
                content=c.content,
                file_path=c.file_path,
                posted_at=c.posted_at,
                comment_seq=c.comment_seq,
            )
            for c in topic.comments
        ],
    )


def fs_experiment_read(plane: FsPlane) -> list[FsExperimentRead]:
    return [
        FsExperimentRead(
            id=e.id,
            slug=e.slug,
            title=e.title,
            description=e.description,
            phase=e.phase,
            creator=e.creator,
            created_at=e.created_at,
            dir_path=e.dir_path,
            plan_path=e.plan_path,
            log_path=e.log_path,
            review_path=e.review_path,
        )
        for e in plane.experiments
    ]


def fs_work_items(project: Project, persona: str) -> list[FsWorkItemRead]:
    plane = plane_for_project(project)
    items: list[FsWorkItem] = []
    for topic in plane.topics:
        items.extend(derive_work(topic, persona))
    return [
        FsWorkItemRead(kind=i.kind, topic_slug=i.topic_slug, title=i.title, round=i.round, detail=i.detail)
        for i in items
    ]


# ---------------------------------------------------------------------------
# waker work 快照：FS topics → DB 兼容 topic-progress 投影
# ---------------------------------------------------------------------------

# FsWorkItem.kind → (DB work item kind, clear_action)
_FS_KIND_MAP: dict[str, tuple[str, str]] = {
    "pending_topic_reply": ("pending_topic_reply", "comment"),
    "round_ack_pending": ("round_ack", "ack"),
}


def persona_short_name(agent: Agent) -> str:
    """agent.name（如 multi-agent-platform-host）→ persona 短名（host）。

    自定义 agent（无 persona 映射）用 name 本身参与文件推导。
    """
    for persona, name in PERSONA_AGENT_NAMES.items():
        if name == agent.name:
            return persona
    return agent.name


def fs_topic_progress_for_agent(db: Session, agent: Agent) -> list[TopicProgressItemRead]:
    """把 ``map/`` 话题的文件存在性待办投影成 topic-progress items。

    供 ``GET /agents/me/work`` 合并——waker（simple-waker）轮询该端点，
    FS 待办由此进入统一 work 快照，无需 waker 侧第二套规则。
    清理语义与 DB 路径一致：写 round<N>-<persona>.md 文件即清除
    pending_topic_reply；host 推进轮次即清除 round_ack。
    """
    if agent.project_id is None:
        return []
    project = db.get(Project, agent.project_id)
    if project is None:
        return []
    persona = persona_short_name(agent)
    agents_by_name = _agents_by_name(db)
    now = datetime.now(timezone.utc)
    results: list[TopicProgressItemRead] = []
    for topic in plane_for_project(project).topics:
        if topic.status != "open":
            continue
        derived = derive_work(topic, persona)
        if not derived:
            continue
        last = topic.comments[-1] if topic.comments else None
        mine = [c for c in topic.comments if c.author == persona]
        my_last = mine[-1] if mine else None
        work_items = [
            TopicWorkItemRead(
                kind=_FS_KIND_MAP[d.kind][0],
                priority="obligation",
                topic_id=topic.id,
                topic_title=topic.title,
                source_comment_id=None,
                thread_root_id=None,
                required_agent_id=agent.id,
                reason="fs_file_missing",
                idempotency_key=f"fs:{_FS_KIND_MAP[d.kind][0]}:{topic.slug}:round{d.round}",
                clear_action=_FS_KIND_MAP[d.kind][1],
                excerpt=d.detail,
                created_at=topic.updated_at or now,
                discussion_round=topic.round,
            )
            for d in derived
        ]
        results.append(
            TopicProgressItemRead(
                topic_id=topic.id,
                topic_title=topic.title,
                discussion_round=topic.round,
                last_comment_author_agent_id=(
                    _agent_id_for(last.author, agents_by_name) if last is not None else None
                ),
                last_comment_author_name=last.author if last is not None else None,
                my_last_comment_id=my_last.id if my_last is not None else None,
                new_comments=[],
                new_comment_count=0,
                work_items=work_items,
            )
        )
    return results


# ---------------------------------------------------------------------------
# 读：DB 兼容转换（供主 /topics 读路径合并）
# ---------------------------------------------------------------------------


def _agents_by_name(db: Session) -> dict[str, Agent]:
    return {agent.name: agent for agent in db.scalars(select(Agent)).all()}


def _agent_id_for(name: str, agents: dict[str, Agent]) -> uuid.UUID:
    agent = agents.get(name)
    if agent is not None:
        return agent.id
    # 名字查不到（例如 agent 未注册）：合成稳定 id，仅用于展示层主键。
    return uuid.uuid5(_PERSONA_NS, name)


def fs_topics_as_summaries(db: Session, project: Project) -> list[TopicSummaryRead]:
    plane = plane_for_project(project)
    agents = _agents_by_name(db)
    results: list[TopicSummaryRead] = []
    for topic in plane.topics:
        last = topic.comments[-1] if topic.comments else None
        now = datetime.now(timezone.utc)
        results.append(
            TopicSummaryRead(
                id=topic.id,
                project_id=project.id,
                creator_agent_id=_agent_id_for(topic.creator, agents),
                creator_name=topic.creator,
                title=topic.title,
                description=topic.description,
                slug=topic.slug,
                status=TopicStatus(topic.status),
                pinned=False,
                discussion_round=topic.round,
                round_summary_count=sum(1 for c in topic.comments if c.is_round_summary),
                comment_count=len(topic.comments),
                experiment_count=0,
                last_comment_id=last.id if last is not None else None,
                last_comment_author_agent_id=_agent_id_for(last.author, agents) if last is not None else None,
                last_comment_author_name=last.author if last is not None else None,
                last_comment_excerpt=last.excerpt if last is not None else None,
                my_comment_count=None,
                dismissed_at=None,
                stale_since=None,
                created_at=topic.created_at or topic.updated_at or now,
                updated_at=topic.updated_at or now,
                archived_at=None,
                close_reason=None,
                close_note=None,
            )
        )
    return results


def fs_topic_as_detail(db: Session, project: Project, topic: FsTopic) -> TopicRead:
    agents = _agents_by_name(db)
    now = datetime.now(timezone.utc)
    summary = next(
        t for t in fs_topics_as_summaries(db, project) if t.id == topic.id
    )
    comments = [
        TopicCommentTreeNode(
            id=c.id,
            topic_id=topic.id,
            author_agent_id=_agent_id_for(c.author, agents),
            author_name=c.author,
            parent_comment_id=None,
            body=c.content or f"See file: {c.file_path}",
            kind=TopicCommentKind(c.kind),
            is_round_summary=c.is_round_summary,
            comment_seq=c.comment_seq,
            created_at=c.posted_at or now,
            unresolved_mentions=[],
            file_path=c.file_path,
            excerpt=c.excerpt,
            children=[],
        )
        for c in topic.comments
    ]
    return TopicRead(**summary.model_dump(), comments=comments)


def find_fs_topic_by_id(
    db: Session, topic_id: uuid.UUID
) -> tuple[Project, FsTopic] | None:
    """跨项目实时解析，按确定性 id 定位 FS topic；未命中返回 None。"""
    for project in db.scalars(select(Project)).all():
        plane = plane_for_project(project)
        for topic in plane.topics:
            if topic.id == topic_id:
                return project, topic
    return None


# ---------------------------------------------------------------------------
# 验证型写：校验后写回 index.md
# ---------------------------------------------------------------------------


def _ensure_fs_host(agent: Agent, topic: FsTopic) -> None:
    # index.md 里 creator 存的是 persona 短名（host），而 bootstrap persona
    # agent 的 name 是 multi-agent-platform-host——先经 persona_short_name
    # 归一到同一侧再比对，否则真正的 host 会被自己的话题挡在门外（403）。
    if persona_short_name(agent) != topic.creator and agent.role != "admin":
        raise ForbiddenError(
            f"Only the fs topic creator ({topic.creator}) or admin can modify it"
        )


def advance_fs_round(
    project: Project,
    slug: str,
    agent: Agent,
    *,
    waive_ack: bool = False,
    mark_ready: bool = False,
    waive_reason: str | None = None,
) -> FsTopicSummaryRead:
    topic = fs_topic_or_raise(project, slug)
    _ensure_fs_host(agent, topic)
    if topic.status != "open":
        raise FsStateError(f"fs topic '{slug}' is {topic.status}; only open topics advance")

    if not waive_ack:
        authors = topic.authors_in_round(topic.round_number)
        missing = [p for p in topic.participants if p != topic.creator and p not in authors]
        if missing:
            raise FsAckPendingError(missing)

    fields: dict[str, object] = {
        "round": "ready" if mark_ready else f"round{topic.round_number + 1}"
    }
    if waive_ack and waive_reason:
        fields["waive_reason"] = waive_reason
    update_topic_index(
        Path(project.workspace_path), slug, content_root=content_root_name(), **fields
    )
    refreshed = fs_topic_or_raise(project, slug)
    return fs_topic_summary(refreshed)


def close_fs_topic(
    project: Project,
    slug: str,
    agent: Agent,
    *,
    close_reason: str | None = None,
    close_note: str | None = None,
) -> FsTopicSummaryRead:
    topic = fs_topic_or_raise(project, slug)
    _ensure_fs_host(agent, topic)
    if topic.status == "closed":
        raise FsStateError(f"fs topic '{slug}' is already closed")
    update_topic_index(
        Path(project.workspace_path),
        slug,
        content_root=content_root_name(),
        status="closed",
        close_reason=close_reason,
        close_note=close_note,
    )
    return fs_topic_summary(fs_topic_or_raise(project, slug))
