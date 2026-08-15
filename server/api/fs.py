"""fs plane API：对 map/ 文件夹事实源的实时解析与验证型写。

- **读**端点每次请求重新扫描文件系统，不经过内容 DB。
- **写**端点只保留带验证/互斥语义的小面（advance-round / close），
  校验通过后写回 index.md；发言本身 = Agent 写一个 .md 文件，零 API。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, Project
from server.domain.schemas import (
    FsAdvanceRoundRequest,
    FsCloseRequest,
    FsExperimentRead,
    FsTopicDetailRead,
    FsTopicSummaryRead,
    FsWorkItemRead,
)
from server.services import fs_source_service as fs_svc
from server.services import permissions as perm

fs_router = APIRouter(tags=["fs"], dependencies=[Depends(bind_background_tasks)])


def _project(db: Session, agent: Agent, project_id: uuid.UUID) -> Project:
    resolved = perm.resolve_project_id_for_agent(agent, project_id)
    perm.ensure_project_access(agent, resolved)
    project = db.get(Project, resolved)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@fs_router.get("/projects/{project_id}/fs/topics", response_model=list[FsTopicSummaryRead])
def list_fs_topics(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[FsTopicSummaryRead]:
    project = _project(db, agent, project_id)
    return [fs_svc.fs_topic_summary(t) for t in fs_svc.plane_for_project(project).topics]


@fs_router.get("/projects/{project_id}/fs/topics/{slug}", response_model=FsTopicDetailRead)
def get_fs_topic(
    project_id: uuid.UUID,
    slug: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> FsTopicDetailRead:
    project = _project(db, agent, project_id)
    try:
        return fs_svc.fs_topic_detail(fs_svc.fs_topic_or_raise(project, slug))
    except fs_svc.FsTopicNotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err


@fs_router.get("/projects/{project_id}/fs/experiments", response_model=list[FsExperimentRead])
def list_fs_experiments(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[FsExperimentRead]:
    project = _project(db, agent, project_id)
    return fs_svc.fs_experiment_read(fs_svc.plane_for_project(project))


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
    except fs_svc.FsTopicNotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err
    except fs_svc.FsAckPendingError as err:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": "round_ack_pending", "missing": err.missing},
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
        event="topic.advance_round",
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
    except fs_svc.FsTopicNotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err
    except fs_svc.FsStateError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err)) from err
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
