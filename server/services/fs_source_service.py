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

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from map_fs import (
    FsComment,
    FsPlane,
    FsTopic,
    FsWorkItem,
    derive_work,
    scan_plane,
    topic_id_for_slug,
    update_topic_index,
)
from map_types.enums import TopicCommentKind, TopicStatus
from map_types.schemas.content_source import ContentSourceMeta
from map_types.schemas.fs import (
    FsCommentRead,
    FsExperimentRead,
    FsPlaneStatusRead,
    FsProjectionDeltaRequest,
    FsProjectionDeltaResult,
    FsProjectionInventoryRead,
    FsProjectionMetaRead,
    FsProjectionObjectHash,
    FsProjectionPushRequest,
    FsTopicDetailRead,
    FsTopicSummaryRead,
    FsWorkItemRead,
    fs_experiment_content_hash,
    fs_projection_content_hash,
    fs_topic_content_hash,
)
from map_types.schemas.project import normalize_content_root
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from server.config import get_settings
from server.domain.models import Agent, AgentRole, FsProjection, Project
from server.domain.schemas import (
    TopicCommentTreeNode,
    TopicProgressItemRead,
    TopicRead,
    TopicSummaryRead,
    TopicWorkItemRead,
)
from server.services.errors import ConflictError, ForbiddenError
from server.services.notification_service import PERSONA_AGENT_NAMES

_PERSONA_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs-persona")
_ROUND_STR_RE = re.compile(r"^round(\d+)$")

# 投影缓存上限：超限 413（payload 是 JSON 快照，超过该量级说明 push 用法
# 变形——应当按项目拆分或改走 Git，而不是把 server 当内容仓库）。
_PROJECTION_MAX_TOPICS = 2000
_PROJECTION_MAX_BYTES = 8 * 1024 * 1024
_PROJECTION_MAX_OBJECT_BYTES = 1024 * 1024


class FsTopicNotFoundError(Exception):
    """话题文件夹不存在。"""


class FsAckPendingError(Exception):
    """本轮还有参与者未发言（ack 未满）。"""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"round ack pending: {', '.join(missing)}")
        self.missing = missing


class FsStateError(Exception):
    """话题当前状态不允许该操作（如已关闭再推进）。"""


class FsPlaneUnavailableError(Exception):
    """server 看不到 workspace 且无投影缓存/evidence 可用。

    部署矩阵显式化的一部分：旧实现里这表现为"静默空列表"或 404，现在
    抛出带修复指引的错误（挂载 workspace / map fs push / 携带 evidence）。
    """


# ---------------------------------------------------------------------------
# 基础：workspace / plane / 可达性
# ---------------------------------------------------------------------------


def content_root_name(project: Project | None = None) -> str:
    """Project-level content root; settings default is only for missing rows."""
    if project is not None and getattr(project, "content_root", None):
        return str(project.content_root)
    return get_settings().content_root


def plane_for_project(project: Project) -> FsPlane:
    return scan_plane(Path(project.workspace_path), content_root_name(project))


def workspace_fs_available(project: Project) -> bool:
    """server 能否直接读到该 project 的内容根目录。"""
    return (Path(project.workspace_path) / content_root_name(project)).is_dir()


def get_fs_projection(db: Session, project: Project) -> FsProjection | None:
    return db.scalar(
        select(FsProjection).where(FsProjection.project_id == project.id)
    )


def projection_payload_topics(row: FsProjection | None) -> list[FsTopicDetailRead]:
    if row is None:
        return []
    payload = row.payload_json or {}
    try:
        return [FsTopicDetailRead.model_validate(t) for t in payload.get("topics", [])]
    except Exception:
        return []  # 脏快照按空处理；下一次 push 覆盖修复


def projection_payload_experiments(row: FsProjection | None) -> list[FsExperimentRead]:
    if row is None:
        return []
    payload = row.payload_json or {}
    try:
        return [FsExperimentRead.model_validate(e) for e in payload.get("experiments", [])]
    except Exception:
        return []


def _ensure_content_root_matches(project: Project, client_root: str | None) -> None:
    expected = content_root_name(project)
    if client_root is None:
        return
    got = normalize_content_root(client_root)
    if got != expected:
        raise ConflictError(
            f"content_root mismatch: project has {expected!r}, client sent {got!r}",
            error="content_root_mismatch",
        )


def content_source_meta(db: Session, project: Project) -> ContentSourceMeta:
    """Build the unified origin envelope for this project's FS plane."""
    content_root_exists = workspace_fs_available(project)
    row = get_fs_projection(db, project)
    now = datetime.now(timezone.utc)
    if content_root_exists:
        return ContentSourceMeta(
            content_source="local-fs",
            source_revision="local-scan",
            source_updated_at=now,
            stale=False,
        )
    if row is None:
        return ContentSourceMeta(
            content_source="none",
            stale=True,
            stale_reason="no projection",
        )
    stale = False
    stale_reason = None
    sla = project.fs_freshness_sla_seconds
    if sla is not None and row.pushed_at is not None:
        pushed = row.pushed_at
        if pushed.tzinfo is None:
            pushed = pushed.replace(tzinfo=timezone.utc)
        if now - pushed > timedelta(seconds=sla):
            stale = True
            stale_reason = "freshness_sla_exceeded"
    return ContentSourceMeta(
        content_source="fs-projection",
        source_revision=str(row.revision),
        source_content_hash=row.content_hash,
        source_updated_at=row.pushed_at,
        stale=stale,
        stale_reason=stale_reason,
    )


def fs_plane_status(db: Session, project: Project) -> FsPlaneStatusRead:
    """部署矩阵探测握手：local-fs / projection-cache / detached 三态。"""
    workspace = Path(project.workspace_path)
    workspace_exists = workspace.is_dir()
    content_root_exists = workspace_fs_available(project)
    row = get_fs_projection(db, project)
    source = content_source_meta(db, project)
    if content_root_exists:
        mode = "local-fs"
        hint = ""
    elif row is not None:
        mode = "projection-cache"
        hint = (
            "workspace 不可达，读路径回退到 map fs sync 的投影缓存；"
            "验证型写走 validate → 本地写回 → commit。写文件后 CLI 会自动增量同步。"
        )
    else:
        mode = "detached"
        hint = (
            "server 看不到 workspace（远程/容器部署），FS plane 对 server 不可见："
            "map/ 话题不会出现在列表与 work 待办中。修复：执行 `map fs sync` "
            "（或兼容别名 `map fs push`）上行投影缓存。"
        )
    return FsPlaneStatusRead(
        workspace_path=project.workspace_path,
        content_root=content_root_name(project),
        workspace_exists=workspace_exists,
        content_root_exists=content_root_exists,
        mode=mode,
        projection_pushed_at=row.pushed_at if row is not None else None,
        projection_revision=row.revision if row is not None else None,
        publisher_agent_id=row.publisher_agent_id if row is not None else None,
        consistency_model=("single-publisher-eventual" if row is not None else None),
        hint=hint,
        source=source,
    )


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
    comments: list[_CommentView] = field(default_factory=list)

    def authors_in_round(self, round_number: int) -> set[str]:
        return {c.author for c in self.comments if c.round == round_number}


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
            )
            for c in topic.comments
        ],
    )


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
        )
        for c in detail.comments
    ]
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
        comments=comments,
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
            )
            for c in view.comments
        ],
        declared_participants=[p for p in view.participants if p != view.creator],
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
        return []


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
# 投影缓存：map fs push 上行 / commit 顺带刷新
# ---------------------------------------------------------------------------


class FsProjectionTooLargeError(Exception):
    """投影快照超限（把它当内容仓库用了）。"""


def _is_project_host(agent: Agent, project: Project) -> bool:
    _ = project
    return agent.name == PERSONA_AGENT_NAMES["host"] or agent.name.endswith("-host")


def _is_projection_sync_agent(agent: Agent) -> bool:
    return agent.name.endswith("-sync")


def _project_host_agent(db: Session, project: Project) -> Agent | None:
    agents = list(db.scalars(select(Agent).where(Agent.project_id == project.id)))
    return next((agent for agent in agents if _is_project_host(agent, project)), None)


def _ensure_projection_publisher_allowed(agent: Agent, project: Project) -> None:
    if (
        agent.role == AgentRole.admin
        or _is_project_host(agent, project)
        or _is_projection_sync_agent(agent)
    ):
        return
    raise ForbiddenError(
        "FS projection push is restricted to the project host, admin, or an "
        "explicit *-sync agent"
    )


def _projection_owner_for_first_push(
    db: Session, project: Project, agent: Agent
) -> Agent:
    if _is_project_host(agent, project):
        return agent
    host = _project_host_agent(db, project)
    if host is not None:
        return host
    if agent.role == AgentRole.admin:
        return agent
    raise ForbiddenError(
        "A project host agent must exist before a *-sync agent can publish the FS projection"
    )


def upsert_fs_projection(
    db: Session, project: Project, agent: Agent, payload: FsProjectionPushRequest
) -> FsProjectionMetaRead:
    _ensure_projection_publisher_allowed(agent, project)
    _ensure_content_root_matches(project, payload.content_root)
    _payload_size_ok(payload.topics, payload.experiments)
    computed_hash = fs_projection_content_hash(payload.topics, payload.experiments)
    if payload.content_hash is not None and payload.content_hash != computed_hash:
        raise ConflictError(
            "projection content_hash does not match the canonical topics/experiments payload"
        )
    body = payload.model_dump(mode="json")
    body["content_hash"] = computed_hash
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > _PROJECTION_MAX_BYTES:
        raise FsProjectionTooLargeError(
            f"projection too large: payload exceeds {_PROJECTION_MAX_BYTES} bytes"
        )

    row = get_fs_projection(db, project)
    if row is None:
        if payload.base_revision is not None:
            raise ConflictError("first projection push must not set base_revision")
        owner = _projection_owner_for_first_push(db, project, agent)
        row = FsProjection(
            project_id=project.id,
            client_workspace=payload.client_workspace,
            publisher_agent_id=agent.id,
            owner_agent_id=owner.id,
            revision=1,
        )
        db.add(row)
        row.pushed_by_agent_id = agent.id
        row.payload_json = body
        row.content_hash = computed_hash
        row.pushed_at = datetime.now(timezone.utc)
        db.flush()
        return _projection_meta(row)
    else:
        # migration 048 leaves legacy rows unbound.  The first eligible P0
        # publisher adopts that row without changing its current revision.
        publisher_agent_id = row.publisher_agent_id
        owner_agent_id = row.owner_agent_id
        if publisher_agent_id is None:
            publisher_agent_id = agent.id
            owner_agent_id = _projection_owner_for_first_push(db, project, agent).id
        elif agent.id != row.publisher_agent_id and agent.role != AgentRole.admin:
            raise ConflictError(
                "FS projection is bound to another single publisher; use that publisher "
                "or an admin recovery path"
            )

        # Identical retries are idempotent even if the caller only learned the
        # successful revision after a transport failure.  They still (a) persist
        # the adoption binding for pre-P0 legacy rows — otherwise the remote
        # owner gate stays 403-locked forever because the same-content retry is
        # short-circuited before the binding is written — and (b) refresh
        # pushed_at as a publisher heartbeat so the freshness SLA can recover.
        if row.content_hash == computed_hash:
            if row.publisher_agent_id is None:
                adopted = db.execute(
                    update(FsProjection)
                    .where(
                        FsProjection.id == row.id,
                        FsProjection.publisher_agent_id.is_(None),
                    )
                    .values(
                        publisher_agent_id=publisher_agent_id,
                        owner_agent_id=owner_agent_id,
                        pushed_by_agent_id=agent.id,
                        pushed_at=datetime.now(timezone.utc),
                    )
                )
                db.expire(row)
                db.refresh(row)
                if adopted.rowcount != 1 and (
                    row.publisher_agent_id != agent.id and agent.role != AgentRole.admin
                ):
                    raise ConflictError(
                        "FS projection is bound to another single publisher; use that "
                        "publisher or an admin recovery path"
                    )
            else:
                db.execute(
                    update(FsProjection)
                    .where(
                        FsProjection.id == row.id,
                        FsProjection.revision == row.revision,
                        FsProjection.content_hash == computed_hash,
                    )
                    .values(
                        pushed_by_agent_id=agent.id,
                        pushed_at=datetime.now(timezone.utc),
                    )
                )
                db.expire(row)
                db.refresh(row)
            return _projection_meta(row)
        if payload.base_revision is None:
            raise ConflictError(
                f"projection base_revision is required; current revision is {row.revision}"
            )
        if payload.base_revision != row.revision:
            raise ConflictError(
                f"projection revision conflict: expected {row.revision}, "
                f"got {payload.base_revision}; pull status/diff before retrying"
            )
        pushed_at = datetime.now(timezone.utc)
        result = db.execute(
            update(FsProjection)
            .where(
                FsProjection.id == row.id,
                FsProjection.revision == payload.base_revision,
            )
            .values(
                pushed_by_agent_id=agent.id,
                publisher_agent_id=publisher_agent_id,
                owner_agent_id=owner_agent_id,
                client_workspace=payload.client_workspace,
                payload_json=body,
                content_hash=computed_hash,
                revision=payload.base_revision + 1,
                pushed_at=pushed_at,
            )
        )
        if result.rowcount != 1:
            db.expire(row)
            db.refresh(row)
            raise ConflictError(
                f"projection revision conflict: expected {row.revision}; "
                "another writer committed first"
            )
        db.expire(row)
        db.refresh(row)
    return _projection_meta(row)


def _projection_meta(row: FsProjection, *, project: Project | None = None) -> FsProjectionMetaRead:
    payload = row.payload_json or {}
    return FsProjectionMetaRead(
        pushed_at=row.pushed_at,
        pushed_by_agent_id=row.pushed_by_agent_id,
        publisher_agent_id=row.publisher_agent_id,
        owner_agent_id=row.owner_agent_id,
        client_workspace=row.client_workspace,
        topic_count=len(payload.get("topics", [])),
        experiment_count=len(payload.get("experiments", [])),
        projection_revision=row.revision,
        content_hash=row.content_hash,
        content_root=content_root_name(project) if project is not None else payload.get("content_root"),
    )


def projection_meta(db: Session, project: Project) -> FsProjectionMetaRead | None:
    row = get_fs_projection(db, project)
    return _projection_meta(row, project=project) if row is not None else None


def projection_inventory(db: Session, project: Project) -> FsProjectionInventoryRead | None:
    row = get_fs_projection(db, project)
    if row is None:
        return None
    topics = projection_payload_topics(row)
    experiments = projection_payload_experiments(row)
    objects = [
        FsProjectionObjectHash(
            kind="topic", slug=topic.slug, content_hash=fs_topic_content_hash(topic)
        )
        for topic in topics
    ]
    objects.extend(
        FsProjectionObjectHash(
            kind="experiment",
            slug=experiment.slug,
            content_hash=fs_experiment_content_hash(experiment),
        )
        for experiment in experiments
    )
    objects.sort(key=lambda item: (item.kind, item.slug))
    return FsProjectionInventoryRead(
        projection_revision=row.revision,
        content_hash=row.content_hash or fs_projection_content_hash(topics, experiments),
        content_root=content_root_name(project),
        publisher_agent_id=row.publisher_agent_id,
        pushed_at=row.pushed_at,
        objects=objects,
        source=content_source_meta(db, project),
    )


def _object_payload_bytes(item: FsTopicDetailRead | FsExperimentRead) -> int:
    return len(item.model_dump_json().encode("utf-8"))


def _payload_size_ok(topics: list[FsTopicDetailRead], experiments: list[FsExperimentRead]) -> None:
    if len(topics) > _PROJECTION_MAX_TOPICS:
        raise FsProjectionTooLargeError(
            f"projection too large: {len(topics)} topics (max {_PROJECTION_MAX_TOPICS})"
        )
    if len(experiments) > _PROJECTION_MAX_TOPICS:
        raise FsProjectionTooLargeError(
            f"projection too large: {len(experiments)} experiments (max {_PROJECTION_MAX_TOPICS})"
        )
    for topic in topics:
        size = _object_payload_bytes(topic)
        if size > _PROJECTION_MAX_OBJECT_BYTES:
            raise FsProjectionTooLargeError(
                f"topic {topic.slug!r} exceeds {_PROJECTION_MAX_OBJECT_BYTES} bytes"
            )
    for experiment in experiments:
        size = _object_payload_bytes(experiment)
        if size > _PROJECTION_MAX_OBJECT_BYTES:
            raise FsProjectionTooLargeError(
                f"experiment {experiment.slug!r} exceeds {_PROJECTION_MAX_OBJECT_BYTES} bytes"
            )
    body = {
        "topics": [t.model_dump(mode="json") for t in topics],
        "experiments": [e.model_dump(mode="json") for e in experiments],
    }
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > _PROJECTION_MAX_BYTES:
        raise FsProjectionTooLargeError(
            f"projection too large: payload exceeds {_PROJECTION_MAX_BYTES} bytes"
        )


def apply_fs_projection_delta(
    db: Session,
    project: Project,
    agent: Agent,
    payload: FsProjectionDeltaRequest,
) -> FsProjectionDeltaResult:
    _ensure_projection_publisher_allowed(agent, project)
    _ensure_content_root_matches(project, payload.content_root)
    row = get_fs_projection(db, project)
    if row is None:
        raise ConflictError(
            "no projection exists; run a full map fs sync / push to bootstrap",
            error="fs_projection_missing",
        )
    if agent.id != row.publisher_agent_id and agent.role != AgentRole.admin:
        raise ConflictError(
            "FS projection is bound to another single publisher; use that publisher "
            "or an admin recovery path",
            error="fs_projection_conflict",
        )
    topics = {item.slug: item for item in projection_payload_topics(row)}
    experiments = {item.slug: item for item in projection_payload_experiments(row)}
    tombstones = 0
    applied = 0
    for change in payload.changes:
        kind = change.kind
        slug = change.slug
        if kind == "topic_upsert":
            if not isinstance(change.value, FsTopicDetailRead):
                raise ConflictError(
                    f"topic_upsert {slug!r} requires a topic value",
                    error="fs_projection_delta_invalid",
                )
            topics[slug] = change.value
            applied += 1
        elif kind == "topic_delete":
            existing = topics.get(slug)
            if existing is None:
                raise ConflictError(
                    f"topic_delete {slug!r} does not exist on the current projection",
                    error="fs_projection_delta_invalid",
                )
            if not change.expected_hash:
                raise ConflictError(
                    f"topic_delete {slug!r} requires expected_hash",
                    error="fs_projection_delta_invalid",
                )
            current_hash = fs_topic_content_hash(existing)
            if change.expected_hash != current_hash:
                raise ConflictError(
                    f"topic_delete {slug!r} hash mismatch",
                    error="fs_projection_delta_invalid",
                )
            del topics[slug]
            tombstones += 1
            applied += 1
        elif kind == "experiment_upsert":
            if not isinstance(change.value, FsExperimentRead):
                raise ConflictError(
                    f"experiment_upsert {slug!r} requires an experiment value",
                    error="fs_projection_delta_invalid",
                )
            experiments[slug] = change.value
            applied += 1
        elif kind == "experiment_delete":
            existing = experiments.get(slug)
            if existing is None:
                raise ConflictError(
                    f"experiment_delete {slug!r} does not exist on the current projection",
                    error="fs_projection_delta_invalid",
                )
            if not change.expected_hash:
                raise ConflictError(
                    f"experiment_delete {slug!r} requires expected_hash",
                    error="fs_projection_delta_invalid",
                )
            current_hash = fs_experiment_content_hash(existing)
            if change.expected_hash != current_hash:
                raise ConflictError(
                    f"experiment_delete {slug!r} hash mismatch",
                    error="fs_projection_delta_invalid",
                )
            del experiments[slug]
            tombstones += 1
            applied += 1
        else:
            raise ConflictError(
                f"unknown delta change kind: {kind}",
                error="fs_projection_delta_invalid",
            )

    topic_list = list(topics.values())
    experiment_list = list(experiments.values())
    _payload_size_ok(topic_list, experiment_list)
    result_hash = fs_projection_content_hash(topic_list, experiment_list)
    if result_hash != payload.result_content_hash:
        raise ConflictError(
            "result_content_hash does not match the reconstructed snapshot",
            error="fs_projection_hash_mismatch",
        )
    if not payload.changes or result_hash == row.content_hash:
        # noop delta = publisher heartbeat：刷新 pushed_at 让 freshness SLA
        # 可自愈（同内容定期 sync 证明发布端仍活跃），不 bump revision。
        db.execute(
            update(FsProjection)
            .where(
                FsProjection.id == row.id,
                FsProjection.revision == row.revision,
                FsProjection.content_hash == row.content_hash,
            )
            .values(
                pushed_by_agent_id=agent.id,
                client_workspace=payload.client_workspace,
                pushed_at=datetime.now(timezone.utc),
            )
        )
        db.expire(row)
        db.refresh(row)
        meta = _projection_meta(row, project=project)
        return FsProjectionDeltaResult(**meta.model_dump(), applied_changes=0, tombstones=0, noop=True)
    if payload.base_revision != row.revision:
        raise ConflictError(
            f"projection revision conflict: expected {row.revision}, "
            f"got {payload.base_revision}; pull status/diff before retrying",
            error="fs_projection_conflict",
        )
    body = {
        "client_workspace": payload.client_workspace,
        "content_root": content_root_name(project),
        "topics": [item.model_dump(mode="json") for item in topic_list],
        "experiments": [item.model_dump(mode="json") for item in experiment_list],
        "content_hash": result_hash,
    }
    pushed_at = datetime.now(timezone.utc)
    result = db.execute(
        update(FsProjection)
        .where(
            FsProjection.id == row.id,
            FsProjection.revision == payload.base_revision,
        )
        .values(
            pushed_by_agent_id=agent.id,
            client_workspace=payload.client_workspace,
            payload_json=body,
            content_hash=result_hash,
            revision=payload.base_revision + 1,
            pushed_at=pushed_at,
        )
    )
    if result.rowcount != 1:
        db.expire(row)
        db.refresh(row)
        raise ConflictError(
            f"projection revision conflict: expected {row.revision}; "
            "another writer committed first",
            error="fs_projection_conflict",
        )
    db.expire(row)
    db.refresh(row)
    meta = _projection_meta(row, project=project)
    return FsProjectionDeltaResult(
        **meta.model_dump(),
        applied_changes=applied,
        tombstones=tombstones,
        noop=False,
    )


def apply_fields_to_projection(
    db: Session,
    project: Project,
    slug: str,
    fields: dict[str, str],
    *,
    base_revision: int,
) -> int | None:
    """commit 后把写回字段应用到投影快照（远程模式下保持读视图新鲜）。

    本地可达时投影不是读源，跳过即可；快照无该话题也静默跳过（下次
    push 全量修复）。
    """
    if workspace_fs_available(project):
        return None
    row = get_fs_projection(db, project)
    if row is None:
        raise ConflictError("FS projection missing; run `map fs push` before committing")
    if row.revision != base_revision:
        raise ConflictError(
            f"projection revision conflict: validated at {base_revision}, "
            f"current revision is {row.revision}; local write was not committed"
        )
    # 独立副本：不能就地改 ORM 缓存的 dict——否则新旧值内容相等，
    # SQLAlchemy 会把变更历史剪空，UPDATE 根本不会发出。
    payload = json.loads(json.dumps(row.payload_json or {}, ensure_ascii=False))
    changed = False
    for topic in payload.get("topics", []):
        if topic.get("slug") != slug:
            continue
        if "round" in fields:
            topic["discussion_round"] = fields["round"]
        if "status" in fields:
            topic["status"] = fields["status"]
        if "close_reason" in fields:
            topic["close_reason"] = fields["close_reason"]
        if "waive_reason" in fields:
            topic["waive_reason"] = fields["waive_reason"]
        changed = True
        break
    if changed:
        try:
            topics = [
                FsTopicDetailRead.model_validate(item)
                for item in payload.get("topics", [])
            ]
        except Exception:
            topics = []
        experiments: list[FsExperimentRead] = []
        try:
            experiments = [
                FsExperimentRead.model_validate(item)
                for item in payload.get("experiments", [])
            ]
        except Exception:
            experiments = []
        content_hash = fs_projection_content_hash(topics, experiments)
        payload["content_hash"] = content_hash
        result = db.execute(
            update(FsProjection)
            .where(FsProjection.id == row.id, FsProjection.revision == base_revision)
            .values(
                payload_json=payload,
                content_hash=content_hash,
                revision=base_revision + 1,
                pushed_at=datetime.now(timezone.utc),
            )
        )
        if result.rowcount != 1:
            raise ConflictError(
                "projection revision conflict: another writer committed before this token"
            )
        db.expire(row)
        db.refresh(row)
        return row.revision
    raise ConflictError(
        f"projection topic '{slug}' is missing; push the canonical FS tree before retrying"
    )


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
    for view in plane_views(db, project):
        if view.status != "open":
            continue
        derived = derive_work(_view_as_fs_topic(view), persona)
        if not derived:
            continue
        last = view.comments[-1] if view.comments else None
        mine = [c for c in view.comments if c.author == persona]
        my_last = mine[-1] if mine else None
        work_items = [
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
            "workspace unreachable and no projection is available; run `map fs push`"
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
        "run `map fs sync` first, or mount the workspace (docker-compose.fs.yml)"
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
    if view.status != "open":
        raise FsStateError(f"fs topic '{slug}' is {view.status}; only open topics advance")

    if not waive_ack:
        authors = view.authors_in_round(view.round_number)
        missing = [p for p in view.participants if p != view.creator and p not in authors]
        if missing:
            raise FsAckPendingError(missing)

    fields: dict[str, str] = {
        "round": "ready" if mark_ready else f"round{view.round_number + 1}"
    }
    if waive_ack and waive_reason:
        fields["waive_reason"] = waive_reason
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
    if view.status == "closed":
        raise FsStateError(f"fs topic '{slug}' is already closed")

    fields: dict[str, str] = {"status": "closed"}
    if close_reason:
        fields["close_reason"] = close_reason
    if close_note:
        fields["close_note"] = close_note
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


_NO_DB = _NullSession()  # type: ignore[assignment]


__all__ = [
    "FsAckPendingError",
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
