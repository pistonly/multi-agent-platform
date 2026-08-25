"""实验计划/评审/评论域路由（T17 从 experiments.py 拆出）。

职责边界：M2 plans（list / get version / revise）、M2 reviews（create /
list / withdraw、review-item PATCH）、M2 comments（create / list）。CRUD
与相位流转留在 ``experiments.py``；M3 执行生命周期与执行锁在
``experiment_execution.py``。

模块持有独立 ``APIRouter``（同 tags、同 bind_background_tasks 依赖），
由 ``experiments_router.include_router`` 聚合挂载——URL 契约不变。
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import (
    CommentCreate,
    CommentRead,
    CommentTreeNode,
    PlanRevise,
    PlanVersionRead,
    ReviewCreate,
    ReviewItemRead,
    ReviewItemUpdate,
    ReviewRead,
)
from server.services import comment_service, notification_service, plan_service, review_service
from server.services import permissions as perm
from server.services import project_service as svc

reviews_router = APIRouter(tags=["experiments"], dependencies=[Depends(bind_background_tasks)])


# --- M2: plans ---


@reviews_router.get("/experiments/{experiment_id}/plans", response_model=list[PlanVersionRead])
def list_plans(
    experiment_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[PlanVersionRead]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    plans = plan_service.list_plans(db, experiment_id, limit=limit)
    return [PlanVersionRead.model_validate(p) for p in plans]


@reviews_router.get("/experiments/{experiment_id}/plans/{version}", response_model=PlanVersionRead)
def get_plan_version(
    experiment_id: uuid.UUID,
    version: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlanVersionRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    plan = plan_service.get_plan_version(db, experiment_id, version)
    return PlanVersionRead.model_validate(plan)


@reviews_router.post(
    "/experiments/{experiment_id}/plans",
    response_model=PlanVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def revise_plan(
    experiment_id: uuid.UUID,
    payload: PlanRevise,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlanVersionRead:
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    previous_version = experiment.current_plan_version
    plan = plan_service.revise_plan(db, experiment_id, agent, payload)
    if plan.version != previous_version:
        emit(
            db,
            agent,
            action="plan.revised",
            target_type="plan_version",
            target_id=plan.id,
            project_id=experiment.project_id,
            summary=f"修订计划 v{plan.version}",
            event="plan.revised",
            event_payload={"experiment_id": str(experiment_id), "version": plan.version},
        )
    return PlanVersionRead.model_validate(plan)


# --- M2: reviews ---


@reviews_router.post(
    "/experiments/{experiment_id}/reviews",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    experiment_id: uuid.UUID,
    payload: ReviewCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ReviewRead:
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    review = review_service.create_review(db, experiment_id, agent, payload)
    emit(
        db,
        agent,
        action="review.submitted",
        target_type="review",
        target_id=review.id,
        project_id=experiment.project_id,
        summary="提交评审",
        event="review.submitted",
        event_payload={"experiment_id": str(experiment_id), "review_id": str(review.id)},
    )
    return review_service.review_to_read(db, review)


@reviews_router.get("/experiments/{experiment_id}/reviews", response_model=list[ReviewRead])
def list_reviews(
    experiment_id: uuid.UUID,
    include_archived: bool = Query(
        default=True,
        description=(
            "Whether to include archived reviews. Defaults to true for N=2 "
            "transition (legacy e2e tests + UI initial load expect to see all); "
            "set false to filter to active reviews only."
        ),
    ),
    plan_version: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ReviewRead]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    reviews = review_service.list_reviews(
        db,
        experiment_id,
        include_archived=include_archived,
        plan_version=plan_version,
        limit=limit,
    )
    return [review_service.review_to_read(db, r) for r in reviews]


@reviews_router.post(
    "/experiments/{experiment_id}/reviews/{review_id}/withdraw",
    status_code=status.HTTP_204_NO_CONTENT,
)
def withdraw_review(
    experiment_id: uuid.UUID,
    review_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    review_service.withdraw_review(db, experiment_id, review_id, agent)
    emit(
        db,
        agent,
        action="review.withdrawn",
        target_type="review",
        target_id=review_id,
        project_id=experiment.project_id,
        summary="撤回评审",
        event="review.withdrawn",
        event_payload={"experiment_id": str(experiment_id), "review_id": str(review_id)},
    )


@reviews_router.patch("/review-items/{item_id}", response_model=ReviewItemRead)
def update_review_item(
    item_id: uuid.UUID,
    payload: ReviewItemUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ReviewItemRead:
    perm.ensure_review_item_access(db, agent, item_id)
    item = review_service.update_review_item(db, item_id, agent, payload)
    # Phase 2 D2: kind-directed SSE for the addressed/responded/rebutted
    # transition so the waker can map to ``addressed_review_item``.
    experiment = svc.get_experiment(db, item.review.experiment_id)
    notification_service.emit_kind(
        db,
        project_id=experiment.project_id,
        actor_id=agent.id,
        personas=["host"],
        event="review_item.status_changed",
        summary=f"评审项状态变为 {item.status.value if item.status else 'updated'}（{experiment.title}）",
        target_type="review_item",
        target_id=item.id,
        payload={
            "experiment_id": str(experiment.id),
            "review_id": str(item.review_id),
            "item_id": str(item.id),
            "status": item.status.value if item.status else None,
        },
    )
    return ReviewItemRead.model_validate(item)


# --- M2: comments ---


@reviews_router.post(
    "/experiments/{experiment_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    experiment_id: uuid.UUID,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> CommentRead:
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    comment, unresolved = comment_service.create_comment(db, experiment_id, agent, payload)
    emit(
        db,
        agent,
        action="comment.created",
        target_type="comment",
        target_id=comment.id,
        project_id=experiment.project_id,
        summary="发表评论",
        event="comment.created",
        event_payload={"experiment_id": str(experiment_id), "comment_id": str(comment.id)},
    )
    return comment_service.comment_read(db, comment, unresolved_mentions=unresolved)


@reviews_router.get("/experiments/{experiment_id}/comments")
def list_comments(
    experiment_id: uuid.UUID,
    tree: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[CommentRead] | list[CommentTreeNode]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    comments = comment_service.list_comments(db, experiment_id, limit=limit)
    if tree:
        return comment_service.build_comment_tree(db, comments)
    return comment_service.comments_to_read(db, comments)
