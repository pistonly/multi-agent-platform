"""FS topic 读视图适配层 — T45 拆分自 fs_source_service.py。

双源视图（``_TopicView``：FS 实时解析 ←→ DB 投影）与只读渲染
（summary / detail / experiments / work items）。写门禁与 progress
投影留守宿主 ``fs_source_service``。
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from map_fs import (
    AckPendingError,
    FsActionItem,
    FsComment,
    FsExperiment,
    FsPlane,
    FsTopic,
    FsWorkItem,
    InvalidCloseNoteError,
    InvalidCloseReasonError,
    OpenActionItemsError,
    TopicStateError,
    derive_work,
)
from map_types.enums import TopicCommentKind
from map_types.schemas.fs import (
    FsActionItemRead,
    FsCommentRead,
    FsExperimentRead,
    FsTopicDetailRead,
    FsTopicSummaryRead,
    FsWorkItemRead,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Project
from server.domain.schemas import TopicCommentRead, TopicCommentTreeNode
from server.services.fs_plane_loader import plane_for_project, workspace_fs_available

# T06（2026-08）：投影缓存存储簇（push 全量 / delta 增量 / apply-fields
# 修补 / 行读取与超限防线）已拆至 ``fs_projection_store``。此处 re-export
# 维持既有导入路径（api/fs.py 的 ``fs_svc.upsert_fs_projection``、测试的
# ``_payload_size_ok`` / ``_is_project_host`` 均从本模块取）；依赖方向为
# fs_source_service --top--> fs_projection_store --lazy--> fs_source_service
# （后者的 content_root_name / workspace_fs_available / content_source_meta
# 三个 scan 侧 helper），无导入环。
from server.services.fs_projection_store import (  # noqa: F401
    FsProjectionTooLargeError,
    _is_project_host,
    _payload_size_ok,
    apply_fields_to_projection,
    apply_fs_projection_delta,
    get_fs_projection,
    projection_inventory,
    projection_meta,
    projection_payload_experiments,
    projection_payload_topics,
    upsert_fs_projection,
)

logger = logging.getLogger(__name__)

_PERSONA_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs-persona")
_ROUND_STR_RE = re.compile(r"^round(\d+)$")


class FsTopicNotFoundError(Exception):
    """话题文件夹不存在。"""


# 门禁异常类定义在共享层 map_fs.validation（单一真值，CLI local plane 同源
# 消费）。这里别名到同一类对象：``server/api/fs.py`` 的 isinstance 映射与
# 409 消息格式完全不变。属性/消息契约见 map_fs.validation 对应类 docstring。
FsAckPendingError = AckPendingError
FsInvalidCloseNoteError = InvalidCloseNoteError
FsInvalidCloseReasonError = InvalidCloseReasonError
FsOpenActionItemsError = OpenActionItemsError
FsStateError = TopicStateError


class FsPlaneUnavailableError(Exception):
    """server 看不到 workspace 且无投影缓存/evidence 可用。

    部署矩阵显式化的一部分：旧实现里这表现为"静默空列表"或 404，现在
    抛出带修复指引的错误（挂载 workspace / map fs push / 携带 evidence）。
    """


# ---------------------------------------------------------------------------
# 基础：workspace / plane / 可达性
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 统一话题视图：scan 与 projection 两个来源归一到同一形态
# ---------------------------------------------------------------------------


@dataclass
class _CommentView:
    id: uuid.UUID
    round: int
    author: str
    kind: str
    is_round_summary: bool
    excerpt: str
    content: str
    file_path: str
    posted_at: datetime | None
    comment_seq: int
    file_persona: str = ""
    ack_valid: bool = True
    ack_error: str | None = None


@dataclass
class _TopicView:
    slug: str
    id: uuid.UUID
    title: str
    description: str
    status: str
    round: str  # roundN | ready
    round_number: int
    creator: str
    created_at: datetime | None
    updated_at: datetime | None
    dir_path: str
    participants: list[str] = field(default_factory=list)
    ack_participants: list[str] = field(default_factory=list)
    comments: list[_CommentView] = field(default_factory=list)
    action_items: list[FsActionItem] = field(default_factory=list)
    action_items_error: str | None = None
    # T7 I10：关联实验列表（topic_slug == view.slug 过滤）。workspace 可达时
    # 由 _view_from_fs_topic 从 scan_plane 结果透传；projection 路径默认空
    # list（D6 门禁在 projection 路径仍失效，与 round3-host.md 取证更正一致）。
    experiments: list[FsExperiment] = field(default_factory=list)

    def authors_in_round(self, round_number: int) -> set[str]:
        """effective ack authors：与 parser ``FsTopic.authors_in_round`` 同源，
        只统计 frontmatter 合规（ack_valid）的 comment（review 911fdb0e）。
        """
        return {
            c.author for c in self.comments if c.round == round_number and c.ack_valid
        }


def _round_number_of(round_str: str, comments: list[_CommentView]) -> int:
    match = _ROUND_STR_RE.match(round_str)
    if match:
        return int(match.group(1))
    return max((c.round for c in comments), default=1)


def _view_from_fs_topic(topic: FsTopic) -> _TopicView:
    return _TopicView(
        slug=topic.slug,
        id=topic.id,
        title=topic.title,
        description=topic.description,
        status=topic.status,
        round=topic.round,
        round_number=topic.round_number,
        creator=topic.creator,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        dir_path=topic.dir_path,
        participants=list(topic.participants),
        ack_participants=list(topic.ack_participants()),
        comments=[
            _CommentView(
                id=c.id,
                round=c.round,
                author=c.author,
                kind=c.kind,
                is_round_summary=c.is_round_summary,
                excerpt=c.excerpt,
                content=c.content,
                file_path=c.file_path,
                posted_at=c.posted_at,
                comment_seq=c.comment_seq,
                file_persona=c.file_persona,
                ack_valid=c.ack_valid,
                ack_error=c.ack_error,
            )
            for c in topic.comments
        ],
        action_items=list(topic.action_items),
        action_items_error=topic.action_items_error,
        # T7 I10：透传关联实验列表（scan_plane 已按 topic_slug 过滤），
        # 使 server remote close 走 validate_close 第 4 维门禁真正生效。
        experiments=list(topic.experiments),
    )


def _declared_of(creator: str, participants: list[str]) -> list[str]:
    return [p for p in participants if p != creator]


def _view_from_projection(detail: FsTopicDetailRead) -> _TopicView:
    comments = [
        _CommentView(
            id=c.id,
            round=c.round,
            author=c.author,
            kind=c.kind,
            is_round_summary=c.is_round_summary,
            excerpt=c.excerpt,
            content=c.content,
            file_path=c.file_path,
            posted_at=c.posted_at,
            comment_seq=c.comment_seq,
            file_persona=c.file_persona,
            ack_valid=c.ack_valid,
            ack_error=c.ack_error,
        )
        for c in detail.comments
    ]
    declared = list(detail.declared_participants) or _declared_of(detail.creator, list(detail.participants))
    return _TopicView(
        slug=detail.slug,
        id=detail.id,
        title=detail.title,
        description=detail.description,
        status=detail.status,
        round=detail.discussion_round,
        round_number=_round_number_of(detail.discussion_round, comments),
        creator=detail.creator,
        created_at=detail.created_at,
        updated_at=detail.updated_at,
        dir_path=detail.dir_path,
        participants=list(detail.participants),
        ack_participants=[detail.creator] + [p for p in declared if p != detail.creator],
        comments=comments,
        action_items=[
            FsActionItem(
                id=a.id,
                title=a.title,
                owner=a.owner,
                status=a.status,
                evidence=a.evidence,
                reason=a.reason,
                created_at=a.created_at,
            )
            for a in detail.action_items
        ],
        action_items_error=detail.action_items_error,
    )


def _view_as_fs_topic(view: _TopicView) -> FsTopic:
    """视图 → parser FsTopic（复用 derive_work 纯函数）。

    declared_participants 取非 creator 的白名单成员：FsTopic.participants =
    creator ∪ declared ∪ speakers，与视图的 participants 列表等价。
    """
    return FsTopic(
        slug=view.slug,
        id=view.id,
        title=view.title,
        description=view.description,
        status=view.status,
        round=view.round,
        round_number=view.round_number,
        creator=view.creator,
        created_at=view.created_at,
        updated_at=view.updated_at,
        dir_path=view.dir_path,
        comments=[
            FsComment(
                id=c.id,
                topic_slug=view.slug,
                round=c.round,
                author=c.author,
                kind=c.kind,
                is_round_summary=c.is_round_summary,
                excerpt=c.excerpt,
                content=c.content,
                file_path=c.file_path,
                posted_at=c.posted_at,
                comment_seq=c.comment_seq,
                file_persona=c.file_persona,
                ack_valid=c.ack_valid,
                ack_error=c.ack_error,
            )
            for c in view.comments
        ],
        declared_participants=_declared_of(view.creator, list(view.ack_participants))
        or _declared_of(view.creator, list(view.participants)),
        action_items=list(view.action_items),
        action_items_error=view.action_items_error,
        # T7 I10：透传关联实验列表（视图已透传 _view_from_fs_topic 注入），
        # server remote close validate_close 第 4 维门禁真正生效。
        experiments=list(view.experiments),
    )


def plane_views(db: Session, project: Project) -> list[_TopicView]:
    """读路径统一入口：本地实时解析优先，投影缓存回退。"""
    if workspace_fs_available(project):
        return [_view_from_fs_topic(t) for t in plane_for_project(project).topics]
    row = get_fs_projection(db, project)
    return [_view_from_projection(d) for d in projection_payload_topics(row)]


def fs_topic_or_raise(project: Project, slug: str) -> FsTopic:
    plane = plane_for_project(project)
    topic = plane.topic_by_slug(slug)
    if topic is None:
        raise FsTopicNotFoundError(f"fs topic not found: {slug}")
    return topic


# ---------------------------------------------------------------------------
# 读：FS plane schema
# ---------------------------------------------------------------------------


def fs_topic_summary(view: _TopicView) -> FsTopicSummaryRead:
    return FsTopicSummaryRead(
        id=view.id,
        slug=view.slug,
        title=view.title,
        description=view.description,
        status=view.status,
        discussion_round=view.round,
        creator=view.creator,
        comment_count=len(view.comments),
        participants=view.participants,
        created_at=view.created_at,
        updated_at=view.updated_at,
        dir_path=view.dir_path,
    )


def fs_topic_detail(view: _TopicView) -> FsTopicDetailRead:
    return FsTopicDetailRead(
        **fs_topic_summary(view).model_dump(),
        comments=[
            FsCommentRead(
                id=c.id,
                topic_slug=view.slug,
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
            for c in view.comments
        ],
        action_items=[
            FsActionItemRead(
                id=a.id,
                title=a.title,
                owner=a.owner,
                status=a.status,
                evidence=a.evidence,
                reason=a.reason,
                created_at=a.created_at,
            )
            for a in view.action_items
        ],
        action_items_error=view.action_items_error,
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
            current_plan_version=e.current_plan_version,
            executor=e.executor,
            topic=e.topic,
            updated_at=e.updated_at,
            projection_id=e.projection_id,
        )
        for e in plane.experiments
    ]


def fs_experiments_view(db: Session, project: Project) -> list[FsExperimentRead]:
    """实验列表读：本地解析优先，投影缓存回退（与话题读一致）。"""
    if workspace_fs_available(project):
        return fs_experiment_read(plane_for_project(project))
    row = get_fs_projection(db, project)
    if row is None:
        return []
    payload = row.payload_json or {}
    try:
        return [FsExperimentRead.model_validate(e) for e in payload.get("experiments", [])]
    except Exception:
        logger.warning(
            "fs experiments 投影回退读校验失败，按空处理（project=%s revision=%s）",
            project.id,
            getattr(row, "revision", None),
            exc_info=True,
        )
        return []


def fs_work_items(project: Project, persona: str) -> list[FsWorkItemRead]:
    plane = plane_for_project(project)
    items: list[FsWorkItem] = []
    for topic in plane.topics:
        items.extend(derive_work(topic, persona))
    return [
        FsWorkItemRead(
            kind=i.kind,
            topic_slug=i.topic_slug,
            title=i.title,
            round=i.round,
            detail=i.detail,
            suggested_command=i.suggested_command or None,
        )
        for i in items
    ]


# ---------------------------------------------------------------------------
# 实验 0f271f7e A6：agent 名解析 + FS 评论读视图自 fs_source_service 拆入
# （fs_source_service 800 行 cap；依赖方向不变 —— fs_source_service 顶部
# re-import 保持既有 ``fs_svc._agents_by_name`` / ``fs_svc._agent_id_for``
# / ``fs_svc.fs_topic_comments_as_reads`` import 面）。
# ---------------------------------------------------------------------------


def _agents_by_name(db: Session) -> dict[str, Agent]:
    return {agent.name: agent for agent in db.scalars(select(Agent)).all()}


def _agent_id_for(name: str, agents: dict[str, Agent]) -> uuid.UUID:
    agent = agents.get(name)
    if agent is not None:
        return agent.id
    # 名字查不到（例如 agent 未注册）：合成稳定 id，仅用于展示层主键。
    return uuid.uuid5(_PERSONA_NS, name)


def fs_topic_comments_as_reads(
    db: Session,
    view: _TopicView,
    *,
    tree: bool = False,
    limit: int = 100,
) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
    """FS 评论读视图（实验 0f271f7e A5：``topic_db_read_retired=on`` 时
    /topics/{id}/comments 的唯一来源）。

    与 ``fs_topic_as_detail`` 的评论构造同源（author 名字直透、无线程、
    content 缺省回退 "See file: ..."）；FS 无 DB 分页游标，按 comment_seq
    升序取前 limit 条（与 DB 路径 ``topic_comment_order_clauses`` 的取
    头语义对齐）。
    """
    agents = _agents_by_name(db)
    now = datetime.now(timezone.utc)
    ordered = sorted(view.comments, key=lambda c: c.comment_seq)[
        : max(1, min(limit, 500))
    ]
    if tree:
        return [
            TopicCommentTreeNode(
                id=c.id,
                topic_id=view.id,
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
            for c in ordered
        ]
    return [
        TopicCommentRead(
            id=c.id,
            topic_id=view.id,
            author_agent_id=_agent_id_for(c.author, agents),
            author_name=c.author,
            parent_comment_id=None,
            body=c.content or f"See file: {c.file_path}",
            kind=TopicCommentKind(c.kind),
            comment_seq=c.comment_seq,
            created_at=c.posted_at or now,
            file_path=c.file_path,
            excerpt=c.excerpt,
        )
        for c in ordered
    ]
