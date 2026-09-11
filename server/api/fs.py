"""fs plane API：对 map/ 文件夹事实源的实时解析与验证型写。

- **读**端点每次请求重新扫描文件系统（同机部署）；workspace 不可达时
  回退到 ``map sync publish`` 上行的投影缓存，不经过内容 DB。
- **验证型写**拆成两段：``/validate`` 校验权限与 ack 完整性并签发 HMAC
  token + 应写回 fields；CLI 本地写回 index.md 后凭 ``/write-commit``
  审计 + 刷投影缓存。旧的服务端直接写回端点保留（同机部署 / Web UI）。
- ``GET /fs/status`` 是部署矩阵探测握手：local-fs / projection-cache /
  detached 三态显式可见，杜绝静默降级。
- 发言本身 = Agent 写一个 .md 文件，零 API。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from map_types.schemas.fs import (
    FsPlaneStatusRead,
    FsProjectionDeltaRequest,
    FsProjectionDeltaResult,
    FsProjectionInventoryRead,
    FsProjectionMetaRead,
    FsProjectionPushRequest,
    FsWriteCommitRequest,
    FsWriteCommitResponse,
    FsWriteVerdictRead,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, FsWriteReceipt, Project
from server.domain.schemas import (
    FsAdvanceRoundRequest,
    FsCloseRequest,
    FsExperimentRead,
    FsTopicDetailRead,
    FsTopicSummaryRead,
    FsWorkItemRead,
)
from server.services import fs_source_service as fs_svc
from server.services import fs_write_token
from server.services import permissions as perm
from server.services.errors import ConflictError, ForbiddenError

fs_router = APIRouter(tags=["fs"], dependencies=[Depends(bind_background_tasks)])


def _project(db: Session, agent: Agent, project_id: uuid.UUID) -> Project:
    resolved = perm.resolve_project_id_for_agent(agent, project_id)
    perm.ensure_project_access(agent, resolved)
    project = db.get(Project, resolved)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _plane_unavailable_http(err: fs_svc.FsPlaneUnavailableError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"error": "fs_plane_unavailable", "detail": str(err)},
    )


# ---------------------------------------------------------------------------
# 部署矩阵探测握手 + 投影缓存
# ---------------------------------------------------------------------------


@fs_router.get("/projects/{project_id}/fs/status", response_model=FsPlaneStatusRead)
def get_fs_plane_status(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsPlaneStatusRead:
    """workspace 可达性握手：local-fs / projection-cache / detached。"""
    project = _project(db, agent, project_id)
    return fs_svc.fs_plane_status(db, project)


@fs_router.get(
    "/projects/{project_id}/fs/projection",
    response_model=FsProjectionMetaRead | None,
)
def get_fs_projection_meta(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsProjectionMetaRead | None:
    project = _project(db, agent, project_id)
    return fs_svc.projection_meta(db, project)


@fs_router.put("/projects/{project_id}/fs/projection", response_model=FsProjectionMetaRead)
def push_fs_projection(
    project_id: uuid.UUID,
    payload: FsProjectionPushRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsProjectionMetaRead:
    """``map sync publish``：上行 FS plane 投影快照（幂等覆盖）。"""
    project = _project(db, agent, project_id)
    try:
        meta = fs_svc.upsert_fs_projection(db, project, agent, payload)
    except fs_svc.FsProjectionTooLargeError as err:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(err)) from err
    except ForbiddenError as err:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(err)) from err
    except ConflictError as err:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": err.error or "fs_projection_conflict", "detail": str(err)},
        ) from err
    emit(
        db,
        agent,
        action="fs.projection_push",
        target_type="project",
        target_id=project.id,
        project_id=project.id,
        summary=(
            f"[fs] 投影上行：{meta.topic_count} topic(s), "
            f"{meta.experiment_count} experiment(s) rev={meta.projection_revision}"
        ),
        audit_payload={
            "projection_revision": meta.projection_revision,
            "content_hash": meta.content_hash,
            "topic_count": meta.topic_count,
            "experiment_count": meta.experiment_count,
        },
        notify=False,
    )
    return meta


@fs_router.get(
    "/projects/{project_id}/fs/projection/inventory",
    response_model=FsProjectionInventoryRead | None,
)
def get_fs_projection_inventory(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsProjectionInventoryRead | None:
    project = _project(db, agent, project_id)
    return fs_svc.projection_inventory(db, project)


@fs_router.post(
    "/projects/{project_id}/fs/projection/delta",
    response_model=FsProjectionDeltaResult,
)
def apply_fs_projection_delta(
    project_id: uuid.UUID,
    payload: FsProjectionDeltaRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsProjectionDeltaResult:
    project = _project(db, agent, project_id)
    try:
        result = fs_svc.apply_fs_projection_delta(db, project, agent, payload)
    except fs_svc.FsProjectionTooLargeError as err:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(err)) from err
    except ForbiddenError as err:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(err)) from err
    except ConflictError as err:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": err.error or "fs_projection_conflict", "detail": str(err)},
        ) from err
    emit(
        db,
        agent,
        action="fs.projection_delta",
        target_type="project",
        target_id=project.id,
        project_id=project.id,
        summary=(
            f"[fs] 投影增量：rev {payload.base_revision}->{result.projection_revision} "
            f"changes={result.applied_changes} tombstones={result.tombstones} "
            f"noop={result.noop}"
        ),
        audit_payload={
            "base_revision": payload.base_revision,
            "new_revision": result.projection_revision,
            "applied_changes": result.applied_changes,
            "tombstones": result.tombstones,
            "result_hash": result.content_hash,
            "noop": result.noop,
        },
        notify=False,
    )
    return result


# ---------------------------------------------------------------------------
# 读端点
# ---------------------------------------------------------------------------


@fs_router.get("/projects/{project_id}/fs/topics", response_model=list[FsTopicSummaryRead])
def list_fs_topics(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[FsTopicSummaryRead]:
    project = _project(db, agent, project_id)
    return [fs_svc.fs_topic_summary(v) for v in fs_svc.plane_views(db, project)]


@fs_router.get("/projects/{project_id}/fs/topics/{slug}", response_model=FsTopicDetailRead)
def get_fs_topic(
    project_id: uuid.UUID,
    slug: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsTopicDetailRead:
    project = _project(db, agent, project_id)
    for view in fs_svc.plane_views(db, project):
        if view.slug == slug:
            return fs_svc.fs_topic_detail(view)
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"fs topic not found: {slug}")


@fs_router.get("/projects/{project_id}/fs/experiments", response_model=list[FsExperimentRead])
def list_fs_experiments(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[FsExperimentRead]:
    project = _project(db, agent, project_id)
    return fs_svc.fs_experiments_view(db, project)


@fs_router.get("/projects/{project_id}/fs/work", response_model=list[FsWorkItemRead])
def fs_work(
    project_id: uuid.UUID,
    persona: str = Query(description="persona 名字，如 host / participant"),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[FsWorkItemRead]:
    """从文件推导某 persona 的协作待办（waker 可轮询此端点）。"""
    project = _project(db, agent, project_id)
    return fs_svc.fs_work_items(project, persona)


# ---------------------------------------------------------------------------
# 验证型写（新）：validate → CLI 本地写回 → commit
# ---------------------------------------------------------------------------


def _sign_verdict(
    *,
    action: str,
    project: Project,
    view: object,
    fields: dict[str, str],
    evidence: FsTopicDetailRead | None,
    agent: Agent,
    base_revision: int,
) -> FsWriteVerdictRead:
    summary = fs_svc.fs_topic_summary(view)  # type: ignore[arg-type]
    evidence_json = evidence.model_dump_json() if evidence is not None else None
    token, expires_at = fs_write_token.sign_write_token(
        action=action,
        project_id=str(project.id),
        slug=summary.slug,
        fields=fields,
        agent_id=str(agent.id),
        base_revision=base_revision,
        evidence_sha256=fs_write_token.evidence_digest(evidence_json),
    )
    return FsWriteVerdictRead(
        action=action,
        allowed=True,
        slug=summary.slug,
        fields=fields,
        token=token,
        expires_at=expires_at,
        base_revision=base_revision,
        topic=summary,
    )


def _validate_error_http(exc: Exception) -> HTTPException:
    if isinstance(exc, fs_svc.FsTopicNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, fs_svc.FsAckPendingError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "round_ack_pending",
                "missing": exc.missing,
                "missing_reasons": exc.missing_reasons,
            },
        )
    if isinstance(exc, fs_svc.FsOpenActionItemsError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "action_items_open",
                "items": [
                    {"id": item.id, "title": item.title, "owner": item.owner}
                    for item in exc.items
                ],
                "detail": exc.detail,
            },
        )
    if isinstance(exc, fs_svc.FsStateError | ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, ForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, fs_svc.FsPlaneUnavailableError):
        return _plane_unavailable_http(exc)
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@fs_router.post(
    "/projects/{project_id}/fs/topics/{slug}/advance-round/validate",
    response_model=FsWriteVerdictRead,
)
def fs_advance_round_validate(
    project_id: uuid.UUID,
    slug: str,
    payload: FsAdvanceRoundRequest | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsWriteVerdictRead:
    """校验推进轮次，返回应写回的 fields + commit token（不写文件）。"""
    project = _project(db, agent, project_id)
    body = payload or FsAdvanceRoundRequest()
    try:
        view, fields, base_revision = fs_svc.validate_fs_advance_round(
            db,
            project,
            slug,
            agent,
            waive_ack=body.waive_ack,
            mark_ready=body.mark_ready,
            waive_reason=body.waive_reason,
            evidence=body.evidence,
            base_revision=body.base_revision,
        )
    except fs_svc.FsPlaneUnavailableError as err:
        raise _plane_unavailable_http(err) from err
    except fs_svc.FsTopicNotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err
    except fs_svc.FsAckPendingError as err:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "round_ack_pending",
                "missing": err.missing,
                "missing_reasons": err.missing_reasons,
            },
        ) from err
    except (fs_svc.FsStateError, ConflictError, ForbiddenError) as err:
        raise _validate_error_http(err) from err
    # token 绑定 validate 实际确认的 revision（单次读取，杜绝 TOCTOU 窗口）
    return _sign_verdict(
        action="advance-round", project=project, view=view, fields=fields,
        evidence=body.evidence,
        agent=agent,
        base_revision=base_revision,
    )


@fs_router.post(
    "/projects/{project_id}/fs/topics/{slug}/close/validate",
    response_model=FsWriteVerdictRead,
)
def fs_close_validate(
    project_id: uuid.UUID,
    slug: str,
    payload: FsCloseRequest | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsWriteVerdictRead:
    """校验关闭话题，返回应写回的 fields + commit token（不写文件）。"""
    project = _project(db, agent, project_id)
    body = payload or FsCloseRequest()
    try:
        view, fields, base_revision = fs_svc.validate_fs_close(
            db,
            project,
            slug,
            agent,
            close_reason=body.close_reason,
            close_note=body.close_note,
            evidence=body.evidence,
            base_revision=body.base_revision,
        )
    except Exception as err:  # noqa: BLE001 — 统一映射
        raise _validate_error_http(err) from err
    # token 绑定 validate 实际确认的 revision（单次读取，杜绝 TOCTOU 窗口）
    return _sign_verdict(
        action="close", project=project, view=view, fields=fields,
        evidence=body.evidence,
        agent=agent,
        base_revision=base_revision,
    )


_COMMIT_EVENT = {
    "advance-round": (
        "topic.advance_round",
        "topic.round_advanced",
        "[fs] 推进话题轮次至 {round}（{slug}）",
    ),
    "close": (
        "topic.closed",
        "topic.lifecycle.closed",
        "[fs] 关闭话题 {slug}",
    ),
}


@fs_router.post("/projects/{project_id}/fs/write-commit", response_model=FsWriteCommitResponse)
def fs_write_commit(
    project_id: uuid.UUID,
    payload: FsWriteCommitRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsWriteCommitResponse:
    """CLI 本地写回完成后的 commit：验 token → 审计 + 通知 + 刷投影缓存。"""
    if payload.action not in _COMMIT_EVENT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"action must be one of {sorted(_COMMIT_EVENT)}, got '{payload.action}'",
        )
    project = _project(db, agent, project_id)
    try:
        token_payload = fs_write_token.verify_write_token(
            payload.token,
            action=payload.action,
            project_id=str(project.id),
            slug=payload.slug,
            agent_id=str(agent.id),
        )
    except fs_write_token.FsWriteTokenError as err:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"error": "fs_write_token_invalid", "detail": str(err)},
        ) from err

    fields: dict[str, str] = {
        str(k): str(v) for k, v in (token_payload.get("fields") or {}).items()
    }
    nonce = str(token_payload.get("nonce") or "")
    base_revision = int(token_payload.get("base_revision") or 0)
    if db.get(FsWriteReceipt, nonce) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": "fs_write_token_replayed", "detail": "write token already committed"},
        )

    # applied_fields 与签发 fields 不一致 → 客户端写歪了，拒绝审计。
    if payload.applied_fields and payload.applied_fields != fields:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "fs_write_fields_mismatch",
                "expected": fields,
                "applied": payload.applied_fields,
            },
        )

    # 同机部署可复核：index.md 应已带上写回字段（远程模式跳过，凭 token）。
    if fs_svc.workspace_fs_available(project):
        try:
            current = fs_svc.fs_topic_or_raise(project, payload.slug)
        except fs_svc.FsTopicNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err
        mismatches = [
            key
            for key in ("round", "status")
            if key in fields and str(getattr(current, key, None)) != str(fields[key])
        ]
        if mismatches:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "error": "fs_write_not_applied",
                    "detail": f"index.md 未写回字段: {', '.join(sorted(set(mismatches)))}",
                },
            )

    try:
        projection_revision = fs_svc.apply_fields_to_projection(
            db,
            project,
            payload.slug,
            fields,
            base_revision=base_revision,
        )
    except ConflictError as err:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": "fs_projection_conflict", "detail": str(err)},
        ) from err

    db.add(
        FsWriteReceipt(
            nonce=nonce,
            project_id=project.id,
            agent_id=agent.id,
            slug=payload.slug,
            action=payload.action,
            base_revision=base_revision,
        )
    )
    try:
        db.flush()
    except IntegrityError as err:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": "fs_write_token_replayed", "detail": "write token already committed"},
        ) from err

    audit_action, event, summary_tpl = _COMMIT_EVENT[payload.action]
    emit(
        db,
        agent,
        action=audit_action,
        target_type="topic",
        target_id=fs_svc.topic_id_for_slug(payload.slug),
        project_id=project.id,
        summary=summary_tpl.format(round=fields.get("round", "?"), slug=payload.slug),
        event=event,
        event_payload={
            "topic_slug": payload.slug,
            "fields": fields,
            "source": "fs-commit",
        },
    )
    return FsWriteCommitResponse(
        accepted=True,
        action=payload.action,
        slug=payload.slug,
        projection_revision=projection_revision,
    )


# ---------------------------------------------------------------------------
# 验证型写（旧，服务端直接写回）：同机部署 / Web UI
# ---------------------------------------------------------------------------


@fs_router.post(
    "/projects/{project_id}/fs/topics/{slug}/advance-round",
    response_model=FsTopicSummaryRead,
)
def fs_advance_round(
    project_id: uuid.UUID,
    slug: str,
    payload: FsAdvanceRoundRequest | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsTopicSummaryRead:
    project = _project(db, agent, project_id)
    body = payload or FsAdvanceRoundRequest()
    try:
        summary = fs_svc.advance_fs_round(
            project,
            slug,
            agent,
            waive_ack=body.waive_ack,
            mark_ready=body.mark_ready,
            waive_reason=body.waive_reason,
        )
    except fs_svc.FsPlaneUnavailableError as err:
        raise _plane_unavailable_http(err) from err
    except fs_svc.FsTopicNotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err
    except fs_svc.FsAckPendingError as err:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "round_ack_pending",
                "missing": err.missing,
                "missing_reasons": err.missing_reasons,
            },
        ) from err
    except fs_svc.FsStateError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err)) from err
    emit(
        db,
        agent,
        action="topic.advance_round",
        target_type="topic",
        target_id=summary.id,
        project_id=project.id,
        summary=f"[fs] 推进话题轮次至 {summary.discussion_round}",
        # v0.13 M58：事件名对齐 WAKEABLE_NOTIFICATION_EVENTS 白名单的
        # ``topic.round_advanced``（与 DB 路径 topic_lifecycle_service 一致），
        # 否则 FS 轮次推进永远不产生 wakeable 通知，participant 唤醒链路断裂。
        # action 保持审计动作名不变。
        event="topic.round_advanced",
        event_payload={
            "topic_slug": summary.slug,
            "discussion_round": summary.discussion_round,
            "source": "fs",
        },
    )
    return summary


@fs_router.post(
    "/projects/{project_id}/fs/topics/{slug}/close",
    response_model=FsTopicSummaryRead,
)
def fs_close_topic(
    project_id: uuid.UUID,
    slug: str,
    payload: FsCloseRequest | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsTopicSummaryRead:
    project = _project(db, agent, project_id)
    body = payload or FsCloseRequest()
    try:
        summary = fs_svc.close_fs_topic(
            project, slug, agent, close_reason=body.close_reason, close_note=body.close_note
        )
    except fs_svc.FsPlaneUnavailableError as err:
        raise _plane_unavailable_http(err) from err
    except fs_svc.FsTopicNotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err
    except fs_svc.FsStateError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err)) from err
    except fs_svc.FsOpenActionItemsError as err:
        # D2 门禁唯一防线：action-items.yaml 有 open 项 → 409 带 title/owner（A3）
        raise _validate_error_http(err) from err
    emit(
        db,
        agent,
        action="topic.closed",
        target_type="topic",
        target_id=summary.id,
        project_id=project.id,
        summary=f"[fs] 关闭话题「{summary.title}」",
        event="topic.lifecycle.closed",
        event_payload={"topic_slug": summary.slug, "status": "closed", "source": "fs"},
    )
    return summary
