"""map/ 文件夹事实源：实时解析桥接层 + 部署矩阵适配。

职责边界：

- **读**：优先实时解析 ``<workspace>/<content_root>/``（同机部署）；workspace
  不可达（Docker / 远程 server）时回退到 ``map fs push`` 上行的投影缓存，
  产出统一的 ``_TopicView`` 供主读路径（/topics 合并、/agents/me/work）使用。
- **可达性**：``fs_plane_status`` 显式暴露 local-fs / projection-cache /
  detached 三态，杜绝"扫不到目录静默返回空"的隐性降级。
- **验证型写**：拆成 validate（校验 host 权限 + ack 完整性，签发 HMAC
  token 与应写回的 fields）与 commit（凭 token 审计 + 刷投影缓存）两段；
  CLI 在本地写回 index.md。server 侧直接写回的旧路径保留，供同机部署
  与 Web UI 使用。
- persona 名字 → Agent 的映射仅用于填充 DB 兼容 schema 的 agent id 字段
  （名字查不到时用 uuid5 合成稳定 id），不参与权限判断；FS 写权限只认
  creator 名字或 admin。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from map_fs import (
    AckPendingError,
    InvalidCloseNoteError,
    InvalidCloseReasonError,
    OpenActionItemsError,
    TopicStateError,
    derive_work,
    topic_id_for_slug,
    update_topic_index,
    validate_advance_round,
    validate_close,
)
from map_types.enums import ExperimentPhase, TopicCommentKind, TopicStatus
from map_types.schemas.fs import (
    FsTopicDetailRead,
    FsTopicSummaryRead,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.domain.models import Agent, AgentRole, Experiment, Project
from server.domain.schemas import (
    TopicCommentTreeNode,
    TopicProgressItemRead,
    TopicRead,
    TopicSummaryRead,
    TopicWorkItemRead,
)
from server.services.errors import ConflictError, ForbiddenError

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
from server.services.work_kinds import resolve_kind

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
# waker work 快照：FS topics → DB 兼容 topic-progress 投影
# ---------------------------------------------------------------------------

# FsWorkItem.kind → (DB work item kind, clear_action)
_FS_KIND_MAP: dict[str, tuple[str, str]] = {
    "pending_topic_reply": ("pending_topic_reply", "comment"),
    "round_ack_pending": ("round_ack", "ack"),
}

# 实验"在飞"相位：与 topic_lifecycle_service 的关闭阻塞集一致——这些相位下
# 话题锁实验进行中被 host close 会 409，不能把这样的 ready 话题当 stale 反复催
# （FS 话题无 dismiss 逃生，只能 close/推动）。stale nudge 仅对无活跃实验的话题发。
_ACTIVE_EXPERIMENT_PHASES = (
    ExperimentPhase.draft,
    ExperimentPhase.review,
    ExperimentPhase.approved,
    ExperimentPhase.running,
    ExperimentPhase.result_review,
)


def persona_short_name(agent: Agent) -> str:
    """agent.name（如 multi-agent-platform-host / my-project-host）→ persona 短名（host）。

    与 ``Agent.persona`` 同一规则（尾段 ``-<persona>``）；自定义 agent
    （无 persona 映射）用 name 本身参与文件推导。
    """
    return agent.persona or agent.name


def fs_topic_progress_for_agent(db: Session, agent: Agent) -> list[TopicProgressItemRead]:
    """把 ``map/`` 话题的文件存在性待办投影成 topic-progress items。

    供 ``GET /agents/me/work`` 合并——waker（simple-waker）轮询该端点，
    FS 待办由此进入统一 work 快照，无需 waker 侧第二套规则。
    清理语义与 DB 路径一致：写 round<N>-<persona>.md 文件即清除
    pending_topic_reply；host 推进轮次即清除 round_ack。

    远程部署：读源回退到投影缓存（plane_views），语义不变、新鲜度取决于
    最后一次 ``map fs push``。
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
    # 有活跃实验（draft→result_review）的话题从 stale 候选排除：实验在飞时
    # close 被 topic_lifecycle_service 阻塞，无 dismiss 逃生的 FS 话题若仍被催，
    # 会变成无法清理的常驻义务（DB 路径有 dismiss 逃生，FS 没有）。
    active_exp_topic_ids = set(
        db.scalars(
            select(Experiment.topic_id)
            .where(
                Experiment.project_id == project.id,
                Experiment.phase.in_(_ACTIVE_EXPERIMENT_PHASES),
                Experiment.topic_id.is_not(None),
                Experiment.deleted_at.is_(None),
            )
        ).all()
    )
    stale_cutoff = now - timedelta(
        minutes=get_settings().stale_open_topic_threshold_minutes
    )
    for view in plane_views(db, project):
        if view.status != "open":
            continue
        last = view.comments[-1] if view.comments else None
        mine = [c for c in view.comments if c.author == persona]
        my_last = mine[-1] if mine else None
        derived = derive_work(_view_as_fs_topic(view), persona)
        derived_items = [
            TopicWorkItemRead(
                kind=_FS_KIND_MAP[d.kind][0],
                priority="obligation",
                topic_id=view.id,
                topic_title=view.title,
                source_comment_id=None,
                thread_root_id=None,
                required_agent_id=agent.id,
                reason="fs_file_missing",
                idempotency_key=f"fs:{_FS_KIND_MAP[d.kind][0]}:{view.slug}:round{d.round}",
                clear_action=_FS_KIND_MAP[d.kind][1],
                excerpt=d.detail,
                created_at=view.updated_at or now,
                discussion_round=view.round,
            )
            for d in derived
        ]
        # 收敛即入义务（A2）:open action_items → kind=action_items obligation，
        # owner persona 精确路由（他人不可见），与 stale nudge 同处 work_items
        # 通道、waker 零新逻辑。yaml 格式错漏时不投影（close 门禁会 409 兜底）。
        action_items_out: list[TopicWorkItemRead] = []
        if view.action_items_error is None:
            action_items_out = [
                TopicWorkItemRead(
                    kind=resolve_kind("action_items"),
                    priority="obligation",
                    topic_id=view.id,
                    topic_title=view.title,
                    source_comment_id=None,
                    thread_root_id=None,
                    required_agent_id=agent.id,
                    reason="fs_action_item_open",
                    idempotency_key=f"fs:action_item:{view.slug}:{item.id}",
                    clear_action="complete_or_cancel_action_item",
                    excerpt=f"行动项 #{item.id}: {item.title}",
                    suggested_command=(
                        f"map topic action-item complete --topic {view.slug} "
                        f"--id {item.id} --evidence <commit/pytest/路径>"
                    ),
                    created_at=view.updated_at or now,
                    discussion_round=view.round,
                )
                for item in view.action_items
                if item.status == "open" and item.owner == persona
            ]
        # 久未推进的开放话题 → 对齐 DB todo 桶 stale_open_topics 语义
        # （waker 对该 kind 有关注 & wake.md 有路由）。清理 = close 落结论
        # （done-experiment 话题应 close）或推动轮次；FS 话题 dismiss 是 no-op。
        # 只在**无其他义务源**时发 stale:尚有 open action_items（或 yaml 损坏）
        # 时 close 会被门禁 409 拦截，催 close 会变无法清理的死义务——执行项
        # 清零后 stale 自然接管。
        stale_items: list[TopicWorkItemRead] = []
        if (
            not derived_items
            and not action_items_out
            and view.action_items_error is None
            and persona == view.creator
            and view.id not in active_exp_topic_ids
            and (view.updated_at is None or view.updated_at <= stale_cutoff)
        ):
            stale_items = [
                TopicWorkItemRead(
                    kind=resolve_kind("stale_open_topics"),
                    priority="obligation",
                    topic_id=view.id,
                    topic_title=view.title,
                    source_comment_id=None,
                    thread_root_id=None,
                    required_agent_id=agent.id,
                    reason="fs_topic_stale",
                    idempotency_key=f"fs:stale_open_topics:{view.slug}:open",
                    clear_action="close_or_advance_topic",
                    excerpt="开放话题久未推进，请复盘：已收敛（含已完成实验）应关闭并落结论，否则推动轮次",
                    suggested_command=f"map topic close --topic {view.slug} --note <结论>",
                    created_at=view.updated_at or now,
                    discussion_round=view.round,
                    stale_since=view.updated_at or now,
                )
            ]
        # FS unread_change（contextual，对齐 DB 路径 _unread_change_items 的
        # 「读即清」语义）：最后发言者不是我 → 交接信号。FS 无逐 agent 光标，
        # 用「最新 comment 的作者」近似：对方发言后出现、我自己发言后消失。
        # 仅限白名单（creator ∪ declared ∪ speakers，即 view.participants），
        # reviewer 等旁观者不被讨论流量唤醒。这是 simple-waker 签名唤醒的
        # 交接信号源：对方一发言，下一拍轮询即可唤醒，不再依赖
        # stale_open_topics 心跳（默认 30 分钟阈值）。
        unread_items: list[TopicWorkItemRead] = []
        if last is not None and last.author != persona and persona in view.participants:
            unread_items = [
                TopicWorkItemRead(
                    kind=resolve_kind("unread_change"),
                    priority="contextual",
                    topic_id=view.id,
                    topic_title=view.title,
                    source_comment_id=None,
                    thread_root_id=None,
                    required_agent_id=agent.id,
                    reason="fs_new_comment_from_other",
                    idempotency_key=f"fs:unread_change:{view.slug}:{last.id}",
                    clear_action="read",
                    excerpt=last.excerpt or "",
                    suggested_command=f"map topic comment --topic {view.slug} --file <md>",
                    created_at=last.posted_at or view.updated_at or now,
                    discussion_round=view.round,
                )
            ]
        work_items = [*derived_items, *action_items_out, *unread_items, *stale_items]
        if not work_items:
            continue
        results.append(
            TopicProgressItemRead(
                topic_id=view.id,
                topic_title=view.title,
                discussion_round=view.round,
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
    agents = _agents_by_name(db)
    projection = None if workspace_fs_available(project) else get_fs_projection(db, project)
    owner = (
        db.get(Agent, projection.owner_agent_id)
        if projection is not None and projection.owner_agent_id is not None
        else None
    )
    results: list[TopicSummaryRead] = []
    for view in plane_views(db, project):
        last = view.comments[-1] if view.comments else None
        now = datetime.now(timezone.utc)
        results.append(
            TopicSummaryRead(
                id=view.id,
                project_id=project.id,
                creator_agent_id=(
                    projection.owner_agent_id
                    if projection is not None and projection.owner_agent_id is not None
                    else _agent_id_for(view.creator, agents)
                ),
                creator_name=(owner.name if owner is not None else view.creator),
                title=view.title,
                description=view.description,
                slug=view.slug,
                status=TopicStatus(view.status),
                pinned=False,
                discussion_round=view.round,
                round_summary_count=sum(1 for c in view.comments if c.is_round_summary),
                comment_count=len(view.comments),
                experiment_count=0,
                last_comment_id=last.id if last is not None else None,
                last_comment_author_agent_id=_agent_id_for(last.author, agents) if last is not None else None,
                last_comment_author_name=last.author if last is not None else None,
                last_comment_excerpt=last.excerpt if last is not None else None,
                my_comment_count=None,
                dismissed_at=None,
                stale_since=None,
                created_at=view.created_at or view.updated_at or now,
                updated_at=view.updated_at or now,
                archived_at=None,
                close_reason=None,
                close_note=None,
                content_source=("fs-local" if projection is None else "fs-projection"),
                source=content_source_meta(db, project),
            )
        )
    return results


def fs_topic_as_detail(db: Session, project: Project, view: _TopicView) -> TopicRead:
    agents = _agents_by_name(db)
    projection = None if workspace_fs_available(project) else get_fs_projection(db, project)
    owner = (
        db.get(Agent, projection.owner_agent_id)
        if projection is not None and projection.owner_agent_id is not None
        else None
    )
    now = datetime.now(timezone.utc)
    last = view.comments[-1] if view.comments else None
    summary = TopicSummaryRead(
        id=view.id,
        project_id=project.id,
        creator_agent_id=(owner.id if owner is not None else _agent_id_for(view.creator, agents)),
        creator_name=(owner.name if owner is not None else view.creator),
        title=view.title,
        description=view.description,
        slug=view.slug,
        status=TopicStatus(view.status),
        pinned=False,
        discussion_round=view.round,
        round_summary_count=sum(1 for c in view.comments if c.is_round_summary),
        comment_count=len(view.comments),
        experiment_count=0,
        last_comment_id=last.id if last is not None else None,
        last_comment_author_agent_id=_agent_id_for(last.author, agents) if last is not None else None,
        last_comment_author_name=last.author if last is not None else None,
        last_comment_excerpt=last.excerpt if last is not None else None,
        my_comment_count=None,
        dismissed_at=None,
        stale_since=None,
        created_at=view.created_at or view.updated_at or now,
        updated_at=view.updated_at or now,
        archived_at=None,
        close_reason=None,
        close_note=None,
        content_source=("fs-local" if projection is None else "fs-projection"),
        source=content_source_meta(db, project),
    )
    comments = [
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
        for c in view.comments
    ]
    return TopicRead(**summary.model_dump(), comments=comments)


def find_fs_topic_by_id(
    db: Session, topic_id: uuid.UUID
) -> tuple[Project, _TopicView] | None:
    """跨项目定位 FS topic（本地解析优先，投影缓存回退）。

    workspace 不可达且无投影的 project 直接跳过——旧行为是对每个 project
    全量 scan（目录不存在时静默空扫），多项目注册到同一远程 server 时这
    既是性能坑也是语义坑。
    """
    for project in db.scalars(select(Project)).all():
        if not workspace_fs_available(project) and get_fs_projection(db, project) is None:
            continue
        for view in plane_views(db, project):
            if view.id == topic_id:
                return project, view
    return None


# ---------------------------------------------------------------------------
# 验证型写：validate（校验 + 签发）→ CLI 本地写回 → commit（审计）
# ---------------------------------------------------------------------------


def _ensure_fs_host(agent: Agent, creator: str) -> None:
    # index.md 里 creator 存的是 persona 短名（host），而 bootstrap persona
    # agent 的 name 是 multi-agent-platform-host——先经 persona_short_name
    # 归一到同一侧再比对，否则真正的 host 会被自己的话题挡在门外（403）。
    if persona_short_name(agent) != creator and agent.role != "admin":
        raise ForbiddenError(
            f"Only the fs topic creator ({creator}) or admin can modify it"
        )


def ensure_fs_topic_owner(
    db: Session, project: Project, agent: Agent, view: _TopicView
) -> None:
    """FS 生命周期 owner 门禁；远程模式只认服务端登记 owner_agent_id。"""

    if agent.role == AgentRole.admin:
        return
    if workspace_fs_available(project):
        _ensure_fs_host(agent, view.creator)
        return
    row = get_fs_projection(db, project)
    if row is None or row.owner_agent_id is None:
        raise ForbiddenError(
            "Remote FS projection has no trusted owner; an admin/host must republish it"
        )
    if row.owner_agent_id != agent.id:
        raise ForbiddenError("Only the server-registered FS topic owner or admin can modify it")


def projection_revision_for_write(db: Session, project: Project) -> int:
    if workspace_fs_available(project):
        return 0
    row = get_fs_projection(db, project)
    if row is None:
        raise FsPlaneUnavailableError(
            "workspace unreachable and no projection is available; run `map sync publish --full`"
        )
    return row.revision


def _topic_view_for_validation(
    db: Session,
    project: Project,
    slug: str,
    evidence: FsTopicDetailRead | None,
) -> _TopicView:
    """校验用话题视图：本地实时解析；远程只认已 CAS 发布的投影。

    ``evidence`` 为旧客户端兼容字段，不再覆盖远程权限或 ack 事实。
    """
    if workspace_fs_available(project):
        topic = plane_for_project(project).topic_by_slug(slug)
        if topic is None:
            raise FsTopicNotFoundError(f"fs topic not found: {slug}")
        return _view_from_fs_topic(topic)
    row = get_fs_projection(db, project)
    for detail in projection_payload_topics(row):
        if detail.slug == slug:
            return _view_from_projection(detail)
    raise FsPlaneUnavailableError(
        f"workspace unreachable and no usable projection for '{slug}'; "
        "run `map sync publish` first, or mount the workspace (docker-compose.fs.yml)"
    )


def validate_fs_advance_round(
    db: Session,
    project: Project,
    slug: str,
    agent: Agent,
    *,
    waive_ack: bool = False,
    mark_ready: bool = False,
    waive_reason: str | None = None,
    evidence: FsTopicDetailRead | None = None,
    base_revision: int | None = None,
) -> tuple[_TopicView, dict[str, str], int]:
    """校验推进轮次的前置条件，返回应写回 index.md 的 fields（不写文件）。

    第三项为校验时实际确认的投影 revision，供签发 commit token 直接
    使用——handler 不得二次读取（避免两次读取间的 TOCTOU 窗口）。
    """
    view = _topic_view_for_validation(db, project, slug, evidence)
    ensure_fs_topic_owner(db, project, agent, view)
    current_revision = projection_revision_for_write(db, project)
    if current_revision and base_revision != current_revision:
        raise ConflictError(
            f"projection revision conflict: expected {current_revision}, got {base_revision}"
        )
    # 门禁（status/ack 完整性/fields 产出）委托共享层 map_fs.validation，
    # 与 CLI local plane 单一真值同源；owner gate 与 revision CAS 留在服务端外围。
    fields = validate_advance_round(
        _view_as_fs_topic(view),
        waive_ack=waive_ack,
        waive_reason=waive_reason,
        mark_ready=mark_ready,
    )
    return view, fields, current_revision


def validate_fs_close(
    db: Session,
    project: Project,
    slug: str,
    agent: Agent,
    *,
    close_reason: str | None = None,
    close_note: str | None = None,
    evidence: FsTopicDetailRead | None = None,
    base_revision: int | None = None,
) -> tuple[_TopicView, dict[str, str], int]:
    """校验关闭话题的前置条件，返回应写回的 fields（不写文件）。

    返回值第三项语义同 ``validate_fs_advance_round``。
    """
    view = _topic_view_for_validation(db, project, slug, evidence)
    ensure_fs_topic_owner(db, project, agent, view)
    current_revision = projection_revision_for_write(db, project)
    if current_revision and base_revision != current_revision:
        raise ConflictError(
            f"projection revision conflict: expected {current_revision}, got {base_revision}"
        )
    # 门禁（已关闭拦截 / D2 action-items 零尾款 / fields 产出）委托共享层
    # map_fs.validation，与 CLI local plane 单一真值同源。
    fields = validate_close(
        _view_as_fs_topic(view),
        close_reason=close_reason,
        close_note=close_note,
    )
    return view, fields, current_revision


def advance_fs_round(
    project: Project,
    slug: str,
    agent: Agent,
    *,
    waive_ack: bool = False,
    mark_ready: bool = False,
    waive_reason: str | None = None,
) -> FsTopicSummaryRead:
    """服务端直接写回（同机部署 / Web UI 路径）；validate + update_index。"""
    if not workspace_fs_available(project):
        raise FsPlaneUnavailableError(
            "server cannot write back: workspace unreachable; use the "
            "validate → local write-back → commit flow (CLI does this automatically)"
        )
    # 服务端写回路径以实时解析为准（无 evidence）。
    _view, fields, _revision = validate_fs_advance_round(
        _NO_DB,
        project,
        slug,
        agent,
        waive_ack=waive_ack,
        mark_ready=mark_ready,
        waive_reason=waive_reason,
    )
    update_topic_index(
        Path(project.workspace_path), slug, content_root=content_root_name(project), **fields
    )
    refreshed = _view_from_fs_topic(fs_topic_or_raise(project, slug))
    return fs_topic_summary(refreshed)


def close_fs_topic(
    project: Project,
    slug: str,
    agent: Agent,
    *,
    close_reason: str | None = None,
    close_note: str | None = None,
) -> FsTopicSummaryRead:
    """服务端直接写回（同机部署 / Web UI 路径）；validate + update_index。"""
    if not workspace_fs_available(project):
        raise FsPlaneUnavailableError(
            "server cannot write back: workspace unreachable; use the "
            "validate → local write-back → commit flow (CLI does this automatically)"
        )
    _view, fields, _revision = validate_fs_close(
        _NO_DB,
        project,
        slug,
        agent,
        close_reason=close_reason,
        close_note=close_note,
    )
    update_topic_index(
        Path(project.workspace_path),
        slug,
        content_root=content_root_name(project),
        **fields,
    )
    return fs_topic_summary(_view_from_fs_topic(fs_topic_or_raise(project, slug)))


class _NullSession:
    """服务端写回路径不需要投影缓存（workspace 可达），占位 Session。

    ``_topic_view_for_validation`` 只在 workspace 不可达时才查投影，传占位
    对象即可避免为直接写回路径伪造 DB session。
    """

    def scalar(self, *_args: object, **_kwargs: object) -> None:  # pragma: no cover
        return None


_NO_DB: Session = _NullSession()  # type: ignore[assignment]


__all__ = [
    "FsAckPendingError",
    "FsInvalidCloseNoteError",
    "FsInvalidCloseReasonError",
    "FsOpenActionItemsError",
    "FsPlaneUnavailableError",
    "FsProjectionTooLargeError",
    "FsStateError",
    "FsTopicNotFoundError",
    "advance_fs_round",
    "apply_fields_to_projection",
    "close_fs_topic",
    "apply_fs_projection_delta",
    "content_root_name",
    "content_source_meta",
    "find_fs_topic_by_id",
    "fs_experiment_read",
    "fs_experiments_view",
    "fs_plane_status",
    "fs_topic_as_detail",
    "fs_topic_detail",
    "fs_topic_progress_for_agent",
    "fs_topic_summary",
    "fs_topics_as_summaries",
    "fs_work_items",
    "persona_short_name",
    "plane_for_project",
    "plane_views",
    "projection_inventory",
    "projection_meta",
    "projection_payload_topics",
    "projection_revision_for_write",
    "topic_id_for_slug",
    "upsert_fs_projection",
    "ensure_fs_topic_owner",
    "validate_fs_advance_round",
    "validate_fs_close",
    "workspace_fs_available",
]


# ---------------------------------------------------------------------------
# T45: plane 加载/缓存与 topic 读视图适配拆至 fs_plane_loader / fs_topic_view；
# 此处 re-import（facade）保持既有 ``server.services.fs_source_service``
# import 面（api/ 层多为函数内 lazy import，零改动）。
from server.services.fs_plane_loader import (  # noqa: E402,F401
    _PLANE_CACHE_MAX_ENTRIES,
    _plane_cache,
    _plane_cache_lock,
    _plane_fingerprint,
    _PlaneFingerprint,
    content_root_name,
    content_source_meta,
    fs_plane_status,
    plane_for_project,
    reset_plane_cache,
    workspace_fs_available,
)
from server.services.fs_topic_view import (  # noqa: E402,F401
    _CommentView,
    _declared_of,
    _round_number_of,
    _TopicView,
    _view_as_fs_topic,
    _view_from_fs_topic,
    _view_from_projection,
    fs_experiment_read,
    fs_experiments_view,
    fs_topic_detail,
    fs_topic_or_raise,
    fs_topic_summary,
    fs_work_items,
    plane_views,
)
