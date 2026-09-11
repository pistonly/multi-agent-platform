import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, Project, Topic, TopicStatus
from server.domain.schemas import (
    TopicAdvanceRound,
    TopicCloseRequest,
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
from server.services import feature_flag_service as flags
from server.services import fs_source_service as fs_svc
from server.services import permissions as perm
from server.services import topic_service

topics_router = APIRouter(tags=["topics"], dependencies=[Depends(bind_background_tasks)])

# v0.13 M58：DB 话题写路径整体退役（410 Gone）。CLI 侧已引导性拒绝（exit 2），
# 此为直连 API 消费者的第二道门。退役面 = POST create/close/reopen/advance-round/
# rollback-round/resolve/comments + DELETE；PATCH 仅保留 archived 子字段
# （topic migrate 收尾归档依赖），title/description/pinned 编辑一并 410。
# 读路径（GET*）与 POST dismiss 保留。
_RETIRED_WRITE_HINTS: dict[str, str] = {
    "create": "create topics via `map topic create --title ... --slug <name>` (map/ folder is the source of truth)",
    "close": "close topics via `map topic close --topic <slug> --reason <code> --note ...`",
    "reopen": "topic status lives in map/topics/<slug>/index.md — edit `status` directly and note the reason",
    "advance-round": "advance topics via `map topic advance-round --topic <slug>` (wakeable event included)",
    "rollback-round": "rounds are file facts — remove round<N>-*.md files and fix index.md round/participants",
    "resolve": "decisions ride the close note: `map topic close --topic <slug> --note <decision>`",
    "comment": "comment topics via `map topic comment --topic <slug> --file <md>` (pure local write)",
    "delete": "archive FS topics by moving map/topics/<slug>/ to map/archive/topics/; legacy DB topics stay readable",
    "patch": "only `archived` is maintained (topic migrate finalization); edit title/description/pinned in index.md instead",
}


def _write_retired_410(command: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "topic_write_retired",
            "message": (
                f"DB write path for topic `{command}` retired in v0.13 M58 "
                "(topic writes are FS-only)"
            ),
            "hint": _RETIRED_WRITE_HINTS[command],
        },
    )


# 实验 0f271f7e A5：`topic_db_read_retired=on` 时 DB 内容读路径整体退役
# （fail-closed，引导完成存量迁移）。flag OFF 时以下 helper 不会被触达，
# 读路径与 v0.13 M58 行为逐字节一致。
def _db_read_retired_410(surface: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "topic_db_read_retired",
            "message": (
                f"DB read path for topic `{surface}` retired "
                "(topic_db_read_retired=on; map/ FS is the only content source)"
            ),
            "hint": (
                "this is a legacy DB-only topic — run `map topic migrate` "
                "to move it into map/topics/, then retry"
            ),
        },
    )


def _archived_write_retired_410() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "topic_write_retired",
            "message": (
                "DB archived write retired (topic_db_read_retired=on; "
                "archiving is a filesystem move)"
            ),
            "hint": (
                "archive FS topics via `map topic archive`; legacy DB topics "
                "must finish `map topic migrate` before flipping the flag"
            ),
        },
    )


def _topic_db_read_retired(db: Session, topic_id: uuid.UUID) -> bool:
    """该 topic 的 DB 行存在且其项目已开 ``topic_db_read_retired``。

    以 DB 行的 project_id 为准（跨项目 FS 命中不参与判定）；行不存在
    （FS-native 话题）→ False，由调用方走各自路径。
    """
    topic_row = db.get(Topic, topic_id)
    return (
        topic_row is not None
        and flags.is_topic_db_read_retired_on(db, topic_row.project_id)
    )


@topics_router.post(
    "/projects/{project_id}/topics",
    response_model=TopicSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_topic(
    project_id: uuid.UUID,
    payload: TopicCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    """(Retired v0.13 M58) DB topic creation is gone — 410 with guidance."""
    raise _write_retired_410("create")


@topics_router.get("/projects/{project_id}/topics", response_model=list[TopicSummaryRead])
def list_topics(
    project_id: uuid.UUID,
    response: Response,
    topic_status: TopicStatus | None = Query(default=None, alias="status"),
    creator_agent_id: uuid.UUID | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicSummaryRead]:
    resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
    perm.ensure_project_access(agent, resolved_project_id)
    # fs plane 合并：map/ 文件夹里的话题实时并入列表；slug 冲突时 FS 优先
    # （文件夹是事实源，DB 行只是旧副本）。过滤条件与 DB 侧对齐。
    fs_topics: list[TopicSummaryRead] = []
    project = db.get(Project, resolved_project_id)
    if project is not None:
        fs_topics = fs_svc.fs_topics_as_summaries(db, project)
        if topic_status is not None:
            fs_topics = [t for t in fs_topics if t.status == topic_status]
        if creator_agent_id is not None:
            fs_topics = [t for t in fs_topics if t.creator_agent_id == creator_agent_id]
        if q:
            needle = q.lower()
            fs_topics = [
                t
                for t in fs_topics
                if needle in t.title.lower() or needle in (t.slug or "").lower()
            ]
    if flags.is_topic_db_read_retired_on(db, resolved_project_id):
        # 实验 0f271f7e A5：DB 读路径退役 → 纯 FS 段（含空集），SQL 分页
        # 与 DB total 一并不再触达。
        start = (page - 1) * page_size
        response.headers["X-Total-Count"] = str(len(fs_topics))
        return fs_topics[start : start + page_size]
    if not fs_topics:
        # 无 FS 话题：保持 SQL 分页 + DB total 的原路径。
        topics, total = topic_service.list_topics(
            db,
            resolved_project_id,
            status=topic_status,
            creator_agent_id=creator_agent_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
            viewer_agent_id=agent.id,
        )
    else:
        # 有 FS 话题：合并视图 = FS 段（前置）+ DB 段（slug 反查排除 FS
        # 副本）。T09（2026-08）：DB 段保留 SQL 分页，按段精确续页——
        # 原实现 page_size=None 全量拉取 DB 后内存合并，每页请求成本
        # O(全部话题)；现在 DB 侧只取本页对应的偏移窗口。
        fs_slugs = {t.slug for t in fs_topics if t.slug}
        fs_count = len(fs_topics)
        start = (page - 1) * page_size
        fs_slice = fs_topics[start : start + page_size] if start < fs_count else []
        remaining = page_size - len(fs_slice)
        common: dict[str, object] = {
            "status": topic_status,
            "creator_agent_id": creator_agent_id,
            "q": q,
            "include_archived": include_archived,
            "viewer_agent_id": agent.id,
            "exclude_slugs": fs_slugs or None,
        }
        if remaining > 0:
            db_topics, db_total = topic_service.list_topics(
                db,
                resolved_project_id,
                page=1,
                page_size=remaining,
                offset=max(0, start - fs_count),
                **common,  # type: ignore[arg-type]
            )
        else:
            # 本页完全落在 FS 段内：不取行，仅取 DB total 供 X-Total-Count。
            db_topics = []
            _, db_total = topic_service.list_topics(
                db,
                resolved_project_id,
                page=1,
                page_size=1,
                **common,  # type: ignore[arg-type]
            )
        topics = fs_slice + db_topics
        total = fs_count + db_total
    response.headers["X-Total-Count"] = str(total)
    return topics


@topics_router.get("/topics/{topic_id}", response_model=TopicRead)
def get_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicRead:
    # fs plane：确定性 uuid5 命中 map/ 文件夹话题时直接返回解析结果。
    hit = fs_svc.find_fs_topic_by_id(db, topic_id)
    if hit is not None:
        project, fs_topic = hit
        perm.ensure_project_access(agent, project.id)
        return fs_svc.fs_topic_as_detail(db, project, fs_topic)
    perm.ensure_topic_access(db, agent, topic_id)
    if _topic_db_read_retired(db, topic_id):
        # 实验 0f271f7e A5：FS miss + DB 行存在 = 未迁移存量，fail-closed。
        raise _db_read_retired_410("detail")
    return topic_service.get_topic_detail(db, topic_id)


@topics_router.patch("/topics/{topic_id}", response_model=TopicSummaryRead)
def update_topic(
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    # v0.13 M58：PATCH 仅保留 archived 子字段（topic migrate 收尾归档依赖）；
    # title / description / pinned 编辑一律 410 引导到 index.md。
    changed_fields = payload.model_dump(exclude_unset=True)
    if set(changed_fields) - {"archived"}:
        raise _write_retired_410("patch")
    if _topic_db_read_retired(db, topic_id):
        # 实验 0f271f7e A5：归档即目录搬移（map topic archive），DB archived
        # 位随读路径一并退役 —— 顺序契约：先跑完 map topic migrate 再翻 flag。
        raise _archived_write_retired_410()
    # Archive/undo is a project-level operation: any project member may archive
    # or restore a topic (docs/CLI.md archive spec — mirrors the experiment side
    # which uses ``ensure_experiment_access``).
    perm.ensure_topic_access(db, agent, topic_id)
    topic = topic_service.update_topic(db, topic_id, payload)
    return topic_service.topic_summary(db, topic)


@topics_router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    """(Retired v0.13 M58) DB delete is gone — 410 with guidance."""
    raise _write_retired_410("delete")


@topics_router.post("/topics/{topic_id}/close", response_model=TopicSummaryRead)
def close_topic(
    topic_id: uuid.UUID,
    payload: TopicCloseRequest | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    """(Retired v0.13 M58) DB close is gone — decisions ride the FS close note."""
    raise _write_retired_410("close")


@topics_router.post("/topics/{topic_id}/dismiss", response_model=TopicSummaryRead)
def dismiss_my_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    """Host-only: hide an open topic from the creator's /todos.

    Auto re-surfaces when the topic gets new activity (e.g. new comments).
    """
    topic = topic_service.dismiss_topic(db, agent=agent, topic_id=topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic_service.topic_summary(db, topic)


@topics_router.post("/topics/{topic_id}/reopen", response_model=TopicSummaryRead)
def reopen_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    """(Retired v0.13 M58) DB reopen is gone — edit `status` in index.md."""
    raise _write_retired_410("reopen")


@topics_router.post("/topics/{topic_id}/advance-round", response_model=TopicSummaryRead)
def advance_topic_round(
    topic_id: uuid.UUID,
    payload: TopicAdvanceRound | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    """(Retired v0.13 M58) DB round advance is gone — use `map topic advance-round`."""
    raise _write_retired_410("advance-round")


@topics_router.post("/topics/{topic_id}/rollback-round", response_model=TopicSummaryRead)
def rollback_topic_round(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicSummaryRead:
    """(Retired v0.13 M58) DB rollback is gone — FS rounds are file facts."""
    raise _write_retired_410("rollback-round")


@topics_router.post("/topics/{topic_id}/resolve", response_model=TopicDecisionRead)
def resolve_topic(
    topic_id: uuid.UUID,
    payload: TopicResolve,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicDecisionRead:
    """(Retired v0.13 M58) DB resolve is gone — decisions ride the FS close note."""
    raise _write_retired_410("resolve")


@topics_router.post(
    "/topics/{topic_id}/comments",
    response_model=TopicCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_topic_comment(
    topic_id: uuid.UUID,
    payload: TopicCommentCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> TopicCommentRead:
    """(Retired v0.13 M58) DB comments are gone — use `map topic comment`."""
    raise _write_retired_410("comment")


@topics_router.get("/topics/{topic_id}/comments")
def list_topic_comments(
    topic_id: uuid.UUID,
    tree: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
    if _topic_db_read_retired(db, topic_id):
        # 实验 0f271f7e A5：评论唯一来源是 map/ FS；FS miss = 未迁移存量。
        hit = fs_svc.find_fs_topic_by_id(db, topic_id)
        if hit is None:
            raise _db_read_retired_410("comments")
        project, view = hit
        perm.ensure_project_access(agent, project.id)
        return fs_svc.fs_topic_comments_as_reads(db, view, tree=tree, limit=limit)
    perm.ensure_topic_access(db, agent, topic_id)
    return topic_service.list_topic_comments(db, topic_id, tree=tree, limit=limit)
