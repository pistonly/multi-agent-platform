import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from map_types.enums import ReviewArchivedReason
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    AgentRole,
    ExperimentPhase,
    PlanVersion,
    Project,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
)
from server.domain.schemas import PlanRevise
from server.domain.state_machine import ReviewItemTransitionContext, validate_review_item_transition
from server.services.errors import ForbiddenError, NotFoundError, StateTransitionError
from server.services.project_service import get_experiment

_STUB_PREFIXES = ("See file: ", "<!-- slim create:")


def resolve_plan_content(db: Session, experiment, plan: PlanVersion) -> str:
    """A3-1（实验 plan-db-content-retirement）：plan 正文读取的唯一收口。

    **flag ``plan_db_content_retired`` OFF（默认）→ 恒等返回
    ``plan.content_md``**，与现状逐字节一致（验收 A1 读路径锚点）。

    flag ON → FS ``plan.md`` 是事实源：

    - **当前版**（``plan.version == experiment.current_plan_version``）：
      一律从 FS 读——DB 里无论存全文（存量未 stub 化）还是 stub，FS 都
      是权威；``plan.md`` 缺失则 **fail-closed** 抛 ``ConflictError``
      （409 + 自助化文案指向 ``map experiment plan materialize``，仿 A1-3
      写门禁先例与 topic_db_read_retired 410 语义）。
    - **stub**（``See file:`` / ``<!-- slim create:`` 开头，任意版本）：
      DB 只有指针，必须从 FS 解引用；文件缺失同样 fail-closed。
    - **历史版全文**：A2-2 stub 化只作用于当前版，历史版本 DB 仍是唯一
      副本（FS plan.md 只镜像当前版），原样返回。

    所有 plan 正文消费方（API 读端点 / bundle / acceptance 解析 /
    evidence 校验）必须经本函数取正文，不得直接摸 ``content_md``。
    """
    from server.services.errors import ConflictError
    from server.services.feature_flag_service import is_plan_db_content_retired_on

    if not is_plan_db_content_retired_on(db, experiment.project_id):
        return plan.content_md

    is_stub = plan.content_md.startswith(_STUB_PREFIXES)
    is_current = plan.version == (experiment.current_plan_version or 0)
    if not is_stub and not is_current:
        return plan.content_md  # 历史版全文：FS 无对应物，DB 仍是唯一副本

    guidance = (
        "plan_db_content_retired=on：FS plan.md 是计划正文事实源，"
        f"但该实验的 plan.md 不可读（experiment id={experiment.id} "
        f"version={plan.version}）。修复：由 creator 跑 "
        "map experiment plan materialize --id <exp-id> 把 DB 正文物化为 "
        "map/experiments/<slug>/plan.md；跨机部署需先同步 map/ 目录。"
    )
    plan_file_path = getattr(experiment, "plan_file_path", None)
    if not plan_file_path:
        raise ConflictError(
            f"{guidance}\n（当前实验无 plan_file_path——从未物化过。）",
            error="plan_md_missing",
        )
    project = db.get(Project, experiment.project_id)
    if project is None or not project.workspace_path:
        raise ConflictError(
            f"{guidance}\n（project workspace 不可达，无法定位 plan.md。）",
            error="plan_md_missing",
        )
    plan_path = Path(project.workspace_path) / plan_file_path
    try:
        return plan_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConflictError(
            f"{guidance}\n（读取 {plan_path} 失败：{exc}）",
            error="plan_md_missing",
        ) from exc


def _resolved_read(db: Session, experiment, plan: PlanVersion):
    """ORM PlanVersion → PlanVersionRead，content_md 经 ``resolve_plan_content``。"""
    from map_types.schemas.plan import PlanVersionRead

    content = resolve_plan_content(db, experiment, plan)
    read = PlanVersionRead.model_validate(plan)
    if content != plan.content_md:
        read = read.model_copy(update={"content_md": content})
    return read


def list_plans_resolved(
    db: Session,
    experiment_id: uuid.UUID,
    *,
    limit: int = 50,
) -> list:
    """API 读面收口（A3-1）：列 plan 版本，content_md 走统一 resolve。"""
    experiment = get_experiment(db, experiment_id)
    plans = list_plans(db, experiment_id, limit=limit)
    return [_resolved_read(db, experiment, p) for p in plans]


def get_plan_version_resolved(db: Session, experiment_id: uuid.UUID, version: int):
    """API 读面收口（A3-1）：单版读取，content_md 走统一 resolve。"""
    experiment = get_experiment(db, experiment_id)
    plan = get_plan_version(db, experiment_id, version)
    return _resolved_read(db, experiment, plan)


def list_plans(
    db: Session,
    experiment_id: uuid.UUID,
    *,
    limit: int = 50,
) -> list[PlanVersion]:
    get_experiment(db, experiment_id)
    stmt = (
        select(PlanVersion)
        .where(PlanVersion.experiment_id == experiment_id)
        .order_by(PlanVersion.version.asc())
        .limit(max(1, min(limit, 200)))
    )
    return list(db.scalars(stmt))


def get_plan_version(db: Session, experiment_id: uuid.UUID, version: int) -> PlanVersion:
    get_experiment(db, experiment_id)
    stmt = select(PlanVersion).where(
        PlanVersion.experiment_id == experiment_id,
        PlanVersion.version == version,
    )
    plan = db.scalar(stmt)
    if plan is None:
        raise NotFoundError(f"Plan version {version} not found")
    return plan


def _count_unclosed_unreasonable_items(db: Session, experiment_id: uuid.UUID) -> int:
    stmt = (
        select(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status.in_(
                (
                    ReviewItemStatus.open,
                    ReviewItemStatus.addressed,
                    ReviewItemStatus.rebutted,
                    ReviewItemStatus.escalated,
                )
            ),
        )
    )
    return len(list(db.scalars(stmt)))


def _fs_plan_matches_payload(db: Session, experiment, payload_content: str) -> bool | None:
    """A1-4（flag on 去重判据）：请求正文是否与 FS ``plan.md`` 字节一致。

    B 点修正（话题 plan-db-content-retirement Round 1）：flag on 后 DB
    ``content_md`` 可能是 stub，全文等值比对恒 False——重复 revise 会误
    bump 版本并触发评审归档 cascade。判据改为对 FS ``plan.md``（CLI 双写
    A1-1 已保证与 DB 接受的正文同步）做字节比较。

    返回 ``None`` = 无法判定（workspace 不可达 / 无 plan_file_path /
    plan.md 缺失，跨机部署合法场景），caller 回退旧等值判据——保守：
    宁可多 bump，不可吞掉真实修订。
    """
    project = db.get(Project, experiment.project_id)
    if project is None or not getattr(experiment, "plan_file_path", None):
        return None
    plan_path = Path(project.workspace_path) / experiment.plan_file_path
    if not plan_path.is_file():
        return None
    try:
        fs_bytes = plan_path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(fs_bytes).hexdigest() == hashlib.sha256(
        payload_content.encode("utf-8")
    ).hexdigest()


def _archive_prior_version_reviews(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    new_version: int,
) -> int:
    """Mark every review whose plan_version < new_version as archived.

    Called from ``revise_plan`` after ``experiment.current_plan_version``
    is bumped. The cascade is idempotent: rows already carrying
    ``archived_at`` are skipped so manual / superseded archives stay
    untouched, and the trigger can safely run on repeat bumps without
    overwriting audit metadata.

    Returns the number of rows newly archived in this call (used by
    end-to-end tests; service callers may ignore).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = select(Review).where(
        Review.experiment_id == experiment_id,
        Review.plan_version < new_version,
        Review.archived_at.is_(None),
    )
    rows = list(db.scalars(stmt))
    for review in rows:
        review.archived_at = now
        review.archived_reason = ReviewArchivedReason.auto
    return len(rows)


def revise_plan(
    db: Session,
    experiment_id: uuid.UUID,
    author: Agent,
    payload: PlanRevise,
) -> PlanVersion:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != author.id and author.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can revise the plan")
    if experiment.phase not in (
        ExperimentPhase.draft,
        ExperimentPhase.review,
        ExperimentPhase.running,
    ):
        raise StateTransitionError(
            "Plan can only be revised in draft, review, or running phase"
        )

    # a764abf6 I1.(a): enforce plan frontmatter lint at revise time so
    # missing required fields raise STATE_MACHINE_PLAN_MARKER_MISSING
    # before any version bump / archive cascade runs.
    from server.services.feature_flag_service import is_plan_db_content_retired_on
    from server.services.plan_marker_service import assert_plan_frontmatter_ok

    assert_plan_frontmatter_ok(payload.content_md)
    plan_db_retired = is_plan_db_content_retired_on(db, experiment.project_id)

    if not payload.addressed_item_ids and experiment.current_plan_version > 0:
        current_plan = db.scalar(
            select(PlanVersion).where(
                PlanVersion.experiment_id == experiment.id,
                PlanVersion.version == experiment.current_plan_version,
            )
        )
        if current_plan is not None:
            content_same = current_plan.content_md == payload.content_md
            # A1-4（B 点修正，话题 Round 1 定案）：flag on 后 DB content_md
            # 可能是 stub，上面的全文等值恒 False——对同一 FS plan.md 的重复
            # revise 会误 bump 版本并误归档上一版评审。判据换成对 FS
            # plan.md（A1-1 CLI 双写保证其与 DB 接受的正文同步）做内容哈希
            # 比较；无法判定（跨机 / plan.md 缺失）回退旧等值，保守不误吞
            # 真实修订。flag off：等值判据逐字节不变（回归锚点）。
            if not content_same and plan_db_retired:
                fs_match = _fs_plan_matches_payload(db, experiment, payload.content_md)
                if fs_match is not None:
                    content_same = fs_match
            if content_same and _count_unclosed_unreasonable_items(db, experiment.id) == 0:
                return current_plan

    new_version = experiment.current_plan_version + 1
    plan = PlanVersion(
        experiment_id=experiment.id,
        version=new_version,
        content_md=payload.content_md,
        author_agent_id=author.id,
        change_note=payload.change_note,
    )
    db.add(plan)
    experiment.current_plan_version = new_version

    # I1(b) — auto-archive prior reviews: every review whose plan_version
    # is less than the new current_plan_version is no longer canonical and
    # should be marked archived_reason='auto' + archived_at=now().
    # The early-return branch above (line 82-94) skips this on purpose:
    # when content is unchanged and no items are addressed, no version bump
    # happens so no archive cascade is appropriate.
    _archive_prior_version_reviews(db, experiment_id=experiment.id, new_version=new_version)

    if payload.addressed_item_ids:
        items_stmt = (
            select(ReviewItem)
            .where(ReviewItem.id.in_(payload.addressed_item_ids))
            .options(joinedload(ReviewItem.review))
        )
        items = list(db.scalars(items_stmt))
        if len(items) != len(payload.addressed_item_ids):
            raise NotFoundError("One or more review items not found")
        for item in items:
            if item.review.experiment_id != experiment.id:
                raise ForbiddenError("Review item does not belong to this experiment")
            if item.kind != ReviewItemKind.unreasonable:
                raise StateTransitionError("Only unreasonable items can be addressed")
            ctx = ReviewItemTransitionContext(
                is_creator=True,
                is_reviewer=False,
                is_admin=author.role == AgentRole.admin,
                via_plan_revision=True,
            )
            current = item.status or ReviewItemStatus.open
            validate_review_item_transition(current, ReviewItemStatus.addressed, ctx)
            item.status = ReviewItemStatus.addressed

    # 实验 bd9b21f6 (plan-revision-review-gate) A1+A4: running 中架构级 revise
    # 走显式 --breaking-audit（或 change_note 首行 "breaking:" 前缀）打回
    # pending_review 评审队列；complete 随之被相位门禁真拦截（A2）。change_note
    # 是 reviewer 重评的事实基础，缺失即在 revise 入口拒绝（A4 单一处置，
    # 不设警告分支——警告无机器可判的验收形态）。
    breaking = payload.breaking_audit or (payload.change_note or "").lstrip().startswith(
        "breaking:"
    )
    if breaking:
        note = (payload.change_note or "").strip()
        if len(note) < 10:
            raise StateTransitionError(
                "breaking revise requires a change_note explaining what changed "
                "vs the previous version and why (相对上一版改了什么/为什么两要素); "
                "revision refused"
            )
        if experiment.phase == ExperimentPhase.running:
            experiment.phase = ExperimentPhase.pending_review

    db.commit()
    db.refresh(plan)
    return plan
