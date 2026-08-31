import uuid
from datetime import datetime, timezone

from map_types.enums import ResolutionReason, ReviewSubstituteKind, ReviewVerdict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    ExperimentLog,
    ExperimentPhase,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
)
from server.domain.schemas import ReviewCreate, ReviewItemRead, ReviewItemUpdate, ReviewRead
from server.domain.state_machine import ReviewItemTransitionContext, validate_review_item_transition
from server.services import audit_service
from server.services.errors import ConflictError, ForbiddenError, NotFoundError, StateTransitionError
from server.services.project_service import get_experiment


class CreatorSelfReviewBlockedError(ForbiddenError):
    def __init__(
        self,
        message: str,
        *,
        actor_id: uuid.UUID,
        experiment_id: uuid.UUID,
    ) -> None:
        super().__init__(message)
        self.reason = "creator_self_review_blocked"
        self.actor_id = str(actor_id)
        self.experiment_id = str(experiment_id)


class ApproveEligibilityError(ConflictError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _qualifying_non_creator_reviews(reviews: list[Review], creator_id: uuid.UUID) -> list[Review]:
    return [
        review
        for review in reviews
        if review.reviewer_agent_id != creator_id
        and review.substitute_kind != ReviewSubstituteKind.admin_self_substitute
    ]


def compute_legacy_self_review(db: Session, experiment) -> bool:
    if experiment.phase in (ExperimentPhase.draft, ExperimentPhase.review):
        return False
    reviews = list(
        db.scalars(select(Review).where(Review.experiment_id == experiment.id))
    )
    return len(_qualifying_non_creator_reviews(reviews, experiment.creator_agent_id)) == 0


def _review_has_item_activity(review: Review) -> bool:
    for item in review.items:
        if item.kind != ReviewItemKind.unreasonable:
            continue
        if item.status is not None and item.status != ReviewItemStatus.open:
            return True
    return False


def get_unreasonable_items(db: Session, experiment_id: uuid.UUID) -> list[ReviewItem]:
    stmt = (
        select(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            Review.archived_at.is_(None),
            ReviewItem.kind == ReviewItemKind.unreasonable,
        )
        .options(joinedload(ReviewItem.review))
    )
    return list(db.scalars(stmt))


def count_open_unreasonable_for_experiment(db: Session, experiment_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            Review.archived_at.is_(None),
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
    return db.scalar(stmt) or 0


def open_unreasonable_count_by_experiment(
    db: Session, experiment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Per-experiment count of open unreasonable review items in one GROUP BY.

    Items counted: ``status in (open, addressed, rebutted, escalated)`` —
    mirrors :func:`count_open_unreasonable_for_experiment`. Returns a dict
    keyed by experiment_id; experiments with no open items map to 0.
    Empty input → empty dict without hitting the database.
    """
    if not experiment_ids:
        return {}
    stmt = (
        select(Review.experiment_id, func.count())
        .join(ReviewItem, ReviewItem.review_id == Review.id)
        .where(
            Review.experiment_id.in_(experiment_ids),
            Review.archived_at.is_(None),
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
        .group_by(Review.experiment_id)
    )
    found = {eid: int(count) for eid, count in db.execute(stmt).all()}
    # Fill in zeros for experiments with no open unreasonable items so the
    # caller can index by experiment_id without a defensive ``.get``.
    return {eid: found.get(eid, 0) for eid in experiment_ids}


def has_review_on_older_plan_version(db: Session, experiment) -> bool:
    """True when plan was revised after at least one review on a prior version."""
    stmt = (
        select(func.count())
        .select_from(Review)
        .where(
            Review.experiment_id == experiment.id,
            Review.plan_version < experiment.current_plan_version,
        )
    )
    return (db.scalar(stmt) or 0) > 0


def has_review_on_current_plan_version(db: Session, experiment) -> bool:
    """True when a non-archived review covers the current plan version.

    实验 bd9b21f6 A5: complete 版本核对红旗依赖此判定——``pending_review``
    解除（A7）或正常 review 相位都会为当前 plan_version 落评审，评审覆盖
    当前版本 ⇒ breaking 门禁已走完整条重评链路；反之若 plan 改过（存在更旧
    版本评审）而当前版本无评审覆盖 ⇒ 疑似架构级修订漏标 breaking。
    """
    stmt = (
        select(func.count())
        .select_from(Review)
        .where(
            Review.experiment_id == experiment.id,
            Review.plan_version == experiment.current_plan_version,
            Review.archived_at.is_(None),
        )
    )
    return (db.scalar(stmt) or 0) > 0


def count_open_status_unreasonable_for_experiment(
    db: Session, experiment_id: uuid.UUID
) -> int:
    """Unreasonable items with status=open only (plan revision obligation)."""
    stmt = (
        select(func.count())
        .select_from(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            Review.archived_at.is_(None),
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status == ReviewItemStatus.open,
        )
    )
    return db.scalar(stmt) or 0


def open_status_unreasonable_count_by_experiment(
    db: Session, experiment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Batch mirror of :func:`count_open_status_unreasonable_for_experiment` (T07).

    ``status=open`` only — plan-revision obligation. One GROUP BY for all
    experiments; the map fills zeros so callers can index directly.
    """
    if not experiment_ids:
        return {}
    stmt = (
        select(Review.experiment_id, func.count())
        .join(ReviewItem, ReviewItem.review_id == Review.id)
        .where(
            Review.experiment_id.in_(experiment_ids),
            Review.archived_at.is_(None),
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status == ReviewItemStatus.open,
        )
        .group_by(Review.experiment_id)
    )
    found = {eid: int(count) for eid, count in db.execute(stmt).all()}
    return {eid: found.get(eid, 0) for eid in experiment_ids}


def _prior_version_fully_resolved_from_reviews(
    prior_reviews: list[Review],
    *,
    creator_agent_id: uuid.UUID,
) -> bool:
    """Pure counterpart of :func:`_prior_version_reviews_fully_resolved`.

    Operates on an already-loaded review list so callers can batch-fetch
    reviews for many experiments and evaluate the carve-out in memory.
    """
    prior_non_creator = _qualifying_non_creator_reviews(prior_reviews, creator_agent_id)
    has_unreasonable = False
    for review in prior_non_creator:
        for item in review.items:
            if item.kind != ReviewItemKind.unreasonable:
                continue
            has_unreasonable = True
            # I1(c): the canonical "fully resolved" signal is now
            # ``status=closed`` with ``last_resolution_reason=resolved``.
            # Legacy rows that still carry ``status=resolved`` are accepted
            # for backward compatibility.
            is_fully_resolved = (
                item.status == ReviewItemStatus.closed
                and item.last_resolution_reason == ResolutionReason.resolved
            ) or item.status == ReviewItemStatus.resolved
            if not is_fully_resolved:
                return False
    return has_unreasonable


def _prior_version_reviews_fully_resolved(db: Session, experiment) -> bool:
    """True when a non-creator review on an older plan version raised at least
    one unreasonable item and all such items are now ``resolved``.

    Reviewer resolving every unreasonable item they raised on a prior plan
    version counts as explicit acceptance of the revision, so the creator may
    approve without waiting for a fresh review on the current plan version.
    Reviews with no unreasonable items do not qualify — the reviewer has not
    acknowledged the revision, so a fresh review on the current version is
    still required.
    """
    prior_reviews = list(
        db.scalars(
            select(Review)
            .where(
                Review.experiment_id == experiment.id,
                Review.plan_version < experiment.current_plan_version,
            )
            .options(joinedload(Review.items))
        ).unique()
    )
    return _prior_version_fully_resolved_from_reviews(
        prior_reviews,
        creator_agent_id=experiment.creator_agent_id,
    )


def prior_version_reviews_fully_resolved_by_experiment(
    db: Session,
    experiments: list[Experiment],
) -> dict[uuid.UUID, bool]:
    """Batch form of :func:`_prior_version_reviews_fully_resolved`.

    Returns ``{experiment_id: True}`` when that experiment should be
    excluded from ``pending_reviews`` (prior-version carve-out satisfied).
    Issues at most one ``reviews`` SELECT for the whole input set.

    T8 fix (实验 37bfd973 I1): carve-out is now plan_version-aware.  An
    experiment only satisfies the carve-out when **both** conditions hold:

    (1) all unreasonable items on prior ``plan_version`` are resolved
        (legacy bd9b21f6 A7 carve-out semantics);
    (2) at least one review record exists on the CURRENT ``plan_version``,
        meaning the reviewer has acknowledged the revised plan.

    If condition (2) fails (host revised plan but no reviewer has reviewed
    the new version yet), the experiment stays in ``pending_reviews`` so
    the waker wakes the reviewer and breaks the
    ``revise → carve-out → invisible → never reviewed → never approve``
    deadlock (T5-B e63ec33e 3h stall root cause).
    """
    if not experiments:
        return {}
    exp_by_id = {exp.id: exp for exp in experiments}
    rows = list(
        db.scalars(
            select(Review)
            .where(Review.experiment_id.in_(exp_by_id.keys()))
            .options(joinedload(Review.items))
        ).unique()
    )
    grouped: dict[uuid.UUID, list[Review]] = {eid: [] for eid in exp_by_id}
    has_current_version_review: dict[uuid.UUID, bool] = {eid: False for eid in exp_by_id}
    for review in rows:
        exp = exp_by_id[review.experiment_id]
        if review.plan_version < exp.current_plan_version:
            grouped[review.experiment_id].append(review)
        elif review.plan_version == exp.current_plan_version:
            has_current_version_review[review.experiment_id] = True
    return {
        eid: (
            has_current_version_review.get(eid, False)
            and _prior_version_fully_resolved_from_reviews(
                grouped.get(eid, []),
                creator_agent_id=exp.creator_agent_id,
            )
        )
        for eid, exp in exp_by_id.items()
    }


def assert_approve_eligibility(db: Session, experiment) -> None:
    reviews = list(
        db.scalars(
            select(Review).where(
                Review.experiment_id == experiment.id,
                Review.plan_version == experiment.current_plan_version,
            )
        )
    )
    non_creator_reviews = _qualifying_non_creator_reviews(reviews, experiment.creator_agent_id)
    # Carve-out: a prior-version review whose unreasonable items have all
    # been explicitly ``resolved`` stands in for a fresh current-version
    # review (reviewer has accepted the revision).
    if not non_creator_reviews and not _prior_version_reviews_fully_resolved(db, experiment):
        if not reviews:
            if has_review_on_older_plan_version(db, experiment):
                raise ApproveEligibilityError(
                    "Cannot approve: no review for the current plan version "
                    f"(v{experiment.current_plan_version}); reviewer must submit review "
                    "for this plan version after plan revise",
                    reason="no_review_for_current_plan_version",
                )
            raise ApproveEligibilityError(
                "Cannot approve: no review from a non-creator agent",
                reason="no_review",
            )
        raise ApproveEligibilityError(
            "Cannot approve: only creator reviews exist",
            reason="creator_only_review",
        )
    if count_open_unreasonable_for_experiment(db, experiment.id) > 0:
        raise ApproveEligibilityError(
            "Cannot approve: open unreasonable items remain",
            reason="open_unreasonable_item",
        )


def create_review(
    db: Session,
    experiment_id: uuid.UUID,
    reviewer: Agent,
    payload: ReviewCreate,
) -> Review:
    experiment = get_experiment(db, experiment_id)
    if experiment.phase not in (ExperimentPhase.review, ExperimentPhase.pending_review):
        raise StateTransitionError(
            "Reviews can only be submitted during review phase "
            "(or pending_review for breaking-audit re-review, 实验 bd9b21f6 A7)"
        )
    if (
        reviewer.id == experiment.creator_agent_id
        and reviewer.role != AgentRole.admin
    ):
        raise CreatorSelfReviewBlockedError(
            "Experiment creator cannot submit a review for their own experiment",
            actor_id=reviewer.id,
            experiment_id=experiment_id,
        )

    substitute_kind = ReviewSubstituteKind.none
    if reviewer.role == AgentRole.admin:
        if reviewer.id == experiment.creator_agent_id:
            substitute_kind = ReviewSubstituteKind.admin_self_substitute
        else:
            substitute_kind = ReviewSubstituteKind.admin_for_others
            if not payload.substitute_reason or not payload.substitute_reason.strip():
                raise ConflictError(
                    "Admin substitute review requires substitute_reason",
                )

    existing = db.scalar(
        select(Review).where(
            Review.experiment_id == experiment_id,
            Review.reviewer_agent_id == reviewer.id,
            Review.plan_version == experiment.current_plan_version,
        )
    )
    if existing:
        raise ConflictError("Reviewer already submitted a review for this plan version")

    review = Review(
        experiment_id=experiment_id,
        reviewer_agent_id=reviewer.id,
        plan_version=experiment.current_plan_version,
        substitute_kind=substitute_kind,
    )
    db.add(review)
    db.flush()

    if substitute_kind != ReviewSubstituteKind.none:
        audit_service.log_no_commit(
            db,
            action="review_substitute",
            target_type="experiment",
            agent_id=reviewer.id,
            project_id=experiment.project_id,
            target_id=experiment_id,
            summary=f"Admin substitute review ({substitute_kind.value})",
            payload={
                "actor_id": str(reviewer.id),
                "actor_role": reviewer.role.value,
                "action": "review_substitute",
                "target": str(experiment_id),
                "target_plan_version": experiment.current_plan_version,
                "substitute_kind": substitute_kind.value,
                "reason": payload.substitute_reason,
                "review_id": str(review.id),
            },
        )

    for content in payload.reasonable_items:
        db.add(
            ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.reasonable,
                content=content,
                status=None,
            )
        )
    for content in payload.unreasonable_items:
        db.add(
            ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.unreasonable,
                content=content,
                status=ReviewItemStatus.open,
            )
        )
    db.flush()

    # I1(e): emit a ``review_item.mutation`` audit row for each item created
    # during the review submit so admins can reconstruct the review timeline
    # from ``map audit list --kind review_item_mutation --experiment <id>``.
    for item in review.items:
        audit_service.log_review_item_mutation_no_commit(
            db,
            item=item,
            experiment_id=experiment_id,
            actor_id=reviewer.id,
            project_id=experiment.project_id,
            action="add_item",
            before_state=None,
            after_state=item.status.value if item.status is not None else None,
            reason=None,
        )

    # 实验 bd9b21f6 (plan-revision-review-gate) A7: breaking 打回（pending_review）
    # 期间 reviewer 对当前 plan_version 提交新评审——无 open unreasonable 项即
    # 解除拦截自动迁回 running；仍含则保持 pending_review（complete 持续被拒，
    # 报错可见剩余阻塞数）。判定与 count_open_unreasonable 口径一致，同事务
    # 内完成（review add 与解除判定无竞态）。
    if (
        experiment.phase == ExperimentPhase.pending_review
        and count_open_unreasonable_for_experiment(db, experiment_id) == 0
    ):
        experiment.phase = ExperimentPhase.running

    db.commit()
    stmt = select(Review).where(Review.id == review.id).options(joinedload(Review.items))
    reloaded = db.scalar(stmt)
    assert reloaded is not None
    return reloaded


def withdraw_review(
    db: Session,
    experiment_id: uuid.UUID,
    review_id: uuid.UUID,
    actor: Agent,
) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.phase != ExperimentPhase.review:
        raise StateTransitionError("Reviews can only be withdrawn during review phase")

    stmt = (
        select(Review)
        .where(Review.id == review_id, Review.experiment_id == experiment_id)
        .options(joinedload(Review.items))
    )
    review = db.scalar(stmt)
    if review is None:
        raise NotFoundError("Review not found")
    # I1(d): withdrawing an archived review is structurally meaningless.
    _ensure_review_not_archived(review)
    if review.reviewer_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the review author can withdraw a review")
    if _review_has_item_activity(review):
        raise ConflictError("Cannot withdraw review after review items have been acted on")

    for item in review.items:
        db.delete(item)
    db.delete(review)
    db.commit()


def list_reviews(
    db: Session,
    experiment_id: uuid.UUID,
    *,
    include_archived: bool = False,
    plan_version: int | None = None,
    limit: int = 50,
) -> list[Review]:
    get_experiment(db, experiment_id)
    stmt = select(Review).where(Review.experiment_id == experiment_id)
    if not include_archived:
        stmt = stmt.where(Review.archived_at.is_(None))
    if plan_version is not None:
        stmt = stmt.where(Review.plan_version == plan_version)
    stmt = (
        stmt.options(joinedload(Review.items))
        .order_by(Review.created_at.asc())
        .limit(max(1, min(limit, 200)))
    )
    return list(db.scalars(stmt).unique())


def get_review_item(db: Session, item_id: uuid.UUID) -> ReviewItem:
    stmt = select(ReviewItem).where(ReviewItem.id == item_id).options(joinedload(ReviewItem.review))
    item = db.scalar(stmt)
    if item is None:
        raise NotFoundError("Review item not found")
    return item


def _ensure_review_not_archived(review: Review) -> None:
    """Guard against mutating an archived review.

    I1(d) — once a review row carries ``archived_at`` (set by ``plan_revise``
    auto-archive, manual admin archive, or the backfill marker from
    migration 034), it is no longer canonical and resolve / withdraw /
    update-item flows must refuse with a structured subcode instead of
    silently mutating stale state. The CLI / SDK use this subcode to
    surface the recovery hint pointing at ``--include-archived``.
    """
    if review.archived_at is None:
        return
    reason = (
        review.archived_reason.value
        if review.archived_reason is not None
        else "auto"
    )
    raise StateTransitionError(
        (
            "Review "
            f"{review.id} has been archived (reason={reason}); "
            "resolve / withdraw / update-item flows are not allowed on archived reviews."
        ),
        error_code="REVIEW_ALREADY_ARCHIVED",
        hint=(
            "查看 --include-archived 历史; 若需要修改 item, 请在新的 plan_version 提交新 review。"
        ),
        retryable=False,
    )


def update_review_item(
    db: Session,
    item_id: uuid.UUID,
    actor: Agent,
    payload: ReviewItemUpdate,
) -> ReviewItem:
    item = get_review_item(db, item_id)
    experiment = get_experiment(db, item.review.experiment_id)

    # I1(d): archived reviews are not mutable. Refuse with REVIEW_ALREADY_ARCHIVED
    # so the CLI / SDK can surface the --include-archived recovery hint.
    _ensure_review_not_archived(item.review)

    if item.kind != ReviewItemKind.unreasonable:
        raise StateTransitionError("Only unreasonable items have mutable status")
    if item.status is None:
        raise StateTransitionError("Item has no status")

    is_creator = experiment.creator_agent_id == actor.id
    is_reviewer = item.review.reviewer_agent_id == actor.id
    is_admin = actor.role == AgentRole.admin

    # I1(d): rebutting a single review item is only meaningful while the
    # experiment is in the review phase. Once the experiment has reached
    # ``result_review`` the host creator's intent ("this result should not
    # be accepted") is structurally modelled by ``reject-result``, not by
    # a per-item rebuttal. Surface that as the same structured subcode so
    # the CLI / SDK can route the user to the right command.
    if (
        payload.status == ReviewItemStatus.rebutted
        and is_creator
        and not is_admin
        and experiment.phase != ExperimentPhase.review
    ):
        raise StateTransitionError(
            (
                "Cannot rebut a single review item after the experiment has "
                "left review phase (current phase: "
                f"{experiment.phase.value}). 整个实验结果驳回请让 reviewer / admin 调用 "
                "reject-result。"
            ),
            error_code="REVIEW_REJECT_RESULT_MISUSE",
            hint=(
                "单 item 驳回仅在 review 阶段有效;实验已过 review, 若要驳回整个 result, "
                "请让 reviewer / admin 调用 reject-result, host creator 不可拒绝自己的 result。"
            ),
            retryable=False,
        )

    ctx = ReviewItemTransitionContext(
        is_creator=is_creator,
        is_reviewer=is_reviewer,
        is_admin=is_admin,
    )
    try:
        validate_review_item_transition(item.status, payload.status, ctx)
    except Exception as exc:
        raise StateTransitionError(str(exc)) from exc

    before_state = item.status.value if item.status is not None else None
    item.status, item.last_resolution_reason = _normalize_terminal_status(payload.status)
    item.updated_at = datetime.now(timezone.utc)
    after_state = item.status.value

    # I1(e): emit a ``review_item.mutation`` audit row capturing the
    # before / after state for admin timeline reconstruction. The
    # ``reason`` field stays ``None`` for resolve-item mutations; the
    # free-form reason lives in the experiment log.
    audit_service.log_review_item_mutation_no_commit(
        db,
        item=item,
        experiment_id=experiment.id,
        actor_id=actor.id,
        project_id=experiment.project_id,
        action="resolve_item",
        before_state=before_state,
        after_state=after_state,
        reason=None,
    )
    db.commit()
    db.refresh(item)
    return item


# Terminal status values that the I1(c) state migration collapses into
# ``closed``. Existing callers (CLI ``--status resolved``, review dashboard
# PATCHes, MCP tools) keep sending the legacy values; the API layer rewrites
# them on the way to the database so the new ``closed`` terminal becomes the
# single source of truth.
#
# ``rebutted`` is intentionally absent: it is a *mid-cycle* signal (host
# pushes back on the item while keeping the experiment moving). A reviewer
# may still transition ``rebutted → resolved`` or ``rebutted → open`` to
# accept the rebuttal or restart discussion. Collapsing it to ``closed``
# would break that follow-up path, so the legacy value is preserved as-is
# at the database layer until a follow-up transition completes.
_TERMINAL_STATUS_REASONS: dict[ReviewItemStatus, ResolutionReason] = {
    ReviewItemStatus.resolved: ResolutionReason.resolved,
    ReviewItemStatus.withdrawn: ResolutionReason.superseded,
}


def _normalize_terminal_status(
    target: ReviewItemStatus,
) -> tuple[ReviewItemStatus, ResolutionReason | None]:
    """Rewrite legacy terminal values to ``closed{reason}``.

    Callers that send ``resolved`` / ``rebutted`` / ``withdrawn`` continue to
    succeed; the row is stored as ``closed`` with the matching
    ``last_resolution_reason`` so the new terminal state is the single
    canonical representation in the database.
    """
    reason = _TERMINAL_STATUS_REASONS.get(target)
    if reason is None:
        return target, None
    return ReviewItemStatus.closed, reason


def _latest_verdict_reasons_by_item(
    db: Session, experiment_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    """Look up the most recent accept-result verdict_file and return a map of
    item_id → waived_reason for items where verdict == 'waived'.

    Reads ``experiment_logs.metadata_json.verdict_file`` (written by
    ``phase_service.accept_result`` when a structured verdict file is
    supplied). Returns an empty map when no verdict log exists yet.
    """
    latest = db.scalar(
        select(ExperimentLog)
        .where(
            ExperimentLog.experiment_id == experiment_id,
            ExperimentLog.metadata_json.is_not(None),
        )
        .order_by(ExperimentLog.created_at.desc())
        .limit(1)
    )
    if latest is None or not isinstance(latest.metadata_json, dict):
        return {}
    verdict_file = latest.metadata_json.get("verdict_file")
    if not isinstance(verdict_file, dict):
        return {}
    reasons: dict[uuid.UUID, str] = {}
    for verdict in verdict_file.get("verdicts", []) or []:
        if not isinstance(verdict, dict):
            continue
        if verdict.get("verdict") != ReviewVerdict.waived.value:
            continue
        raw_id = verdict.get("item_id")
        reason = verdict.get("reason")
        if not raw_id or not reason:
            continue
        try:
            reasons[uuid.UUID(str(raw_id))] = str(reason)
        except (ValueError, TypeError):
            continue
    return reasons


def review_to_read(
    db: Session,
    review: Review,
    *,
    verdict_reasons: dict[uuid.UUID, str] | None = None,
) -> ReviewRead:
    """Render a ``Review`` ORM row as the API read model.

    Args:
        db: SQLAlchemy session.
        review: ORM row (must have ``items`` eagerly loaded — see
            :func:`list_reviews` which uses ``joinedload(Review.items)``).
        verdict_reasons: Pre-computed map of ``ReviewItem.id → waived_reason``
            for items where the latest accept-result verdict was ``waived``.
            When ``None`` (the default for single-review callers), the
            latest verdict log is fetched on demand. When the caller is
            rendering multiple reviews for the same experiment (e.g.
            ``get_experiment_bundle``), passing a precomputed map
            collapses R redundant ``SELECT … FROM experiment_logs`` to 1.
    """
    reasons = (
        verdict_reasons
        if verdict_reasons is not None
        else _latest_verdict_reasons_by_item(db, review.experiment_id)
    )
    items = []
    for i in review.items:
        item_read = ReviewItemRead.model_validate(i)
        item_read.waived_reason = reasons.get(i.id)
        items.append(item_read)
    return ReviewRead(
        id=review.id,
        experiment_id=review.experiment_id,
        reviewer_agent_id=review.reviewer_agent_id,
        plan_version=review.plan_version,
        substitute_kind=review.substitute_kind,
        created_at=review.created_at,
        archived_at=review.archived_at,
        archived_reason=review.archived_reason,
        items=items,
    )
