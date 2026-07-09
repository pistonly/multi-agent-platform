from __future__ import annotations

import uuid
from datetime import datetime

from map_types.schemas import ActionItemStalePayload
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import AuditLog, ReviewItem, TopicActionItem
from server.domain.schemas import AuditLogRead


def log_no_commit(
    db: Session,
    *,
    action: str,
    target_type: str,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> AuditLog:
    """Public audit logger that flushes but does NOT commit.

    cleanup experiment (f12a5638) Exp A: previously ``_log_no_commit``,
    a private helper used by 13+ callers in ``topic_service``,
    ``review_service``, ``phase_service``, ``action_item_migration_service``,
    and ``mention_service``. The leading underscore implied "internal"
    but every caller in the codebase uses it directly — the public
    contract is real. Renamed to drop the underscore so the API
    matches its actual scope (callers may legitimately want to log
    multiple audit rows inside a single transaction).
    """
    entry = AuditLog(
        agent_id=agent_id,
        project_id=project_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        payload_json=payload,
    )
    db.add(entry)
    db.flush()
    return entry


# cleanup Exp A: keep the legacy private alias so external callers
# (none in this repo, but downstream SDKs / test suites might) don't
# break. Mark deprecated; remove after one minor version.
_log_no_commit = log_no_commit


def log(
    db: Session,
    *,
    action: str,
    target_type: str,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> AuditLog:
    entry = log_no_commit(
        db,
        action=action,
        target_type=target_type,
        agent_id=agent_id,
        project_id=project_id,
        target_id=target_id,
        summary=summary,
        payload=payload,
    )
    db.commit()
    db.refresh(entry)
    return entry


def query_by_target(
    db: Session,
    target_type: str,
    target_id: uuid.UUID,
) -> list[AuditLogRead]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.target_type == target_type, AuditLog.target_id == target_id)
        .order_by(AuditLog.created_at.desc())
    )
    return [AuditLogRead.model_validate(row) for row in db.scalars(stmt)]


def query_all(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    action: str | None = None,
    experiment_id: uuid.UUID | None = None,
) -> tuple[list[AuditLogRead], int]:
    """Paginated admin audit query with optional filters.

    ``action`` is matched against ``AuditLog.action`` (the audit-log kind,
    e.g. ``review_item.mutation``). ``experiment_id`` filters rows whose
    ``payload_json`` carries the matching experiment id — used by the CLI
    ``map audit list --experiment <id>`` flag so admins can scope the
    timeline to a single experiment without paging the global log.
    """
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    # experiment_id is denormalised into payload_json by the per-event
    # writers; filter at the DB level so paging stays efficient even when
    # the global log grows large.
    if experiment_id is not None:
        experiment_id_str = str(experiment_id)
        stmt = stmt.where(AuditLog.payload_json["experiment_id"].as_string() == experiment_id_str)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return [AuditLogRead.model_validate(row) for row in items], total


# --- Action-item audit events (experiment B / waker escalation 三段式) -----

# Action types live as constants so callers and grep share one source of truth.
# Stale is a *diagnostic* signal, not a state transition: ``complete`` and
# ``cancelled`` flip ``status``; ``stale`` only annotates that the assignee has
# not responded across the three-stage escalation window. ``wake_sent`` marks
# each waker firing so reviewers can grep the escalation timeline.
ACTION_ITEM_STALE = "action_item.stale"
ACTION_ITEM_WAKE_SENT = "action_item.wake_sent"


def _log_action_item_stale_no_commit(
    db: Session,
    *,
    item: TopicActionItem,
    stale_after_attempt: int,
    admin_notified: bool,
    creator_audit_only: bool,
) -> AuditLog:
    """Insert the ``action_item.stale`` audit row without committing.

    Used by ``action_item_service.mark_stale_no_commit`` which must keep the
    status mutation, the ``stale_at`` timestamp, and the audit row in one
    transaction (plan §3 §4 "wake_count 自增与 stale_at 的事务边界" risk).

    Validation mirrors the public ``log_action_item_stale`` helper — any
    rejection raises ``ValueError`` *before* the flush, so a partial insert
    cannot leak.
    """
    if item.owner_agent_id is None:
        raise ValueError(
            "action_item.stale requires owner_agent_id (assignee) — "
            "waker only wakes the owner, so stale cannot fire for unassigned items"
        )
    if item.last_woken_at is None:
        raise ValueError(
            "action_item.stale requires item.last_woken_at — "
            "stale fires only after at least one wake has happened"
        )
    if stale_after_attempt < 1:
        raise ValueError(
            f"stale_after_attempt must be >= 1 (got {stale_after_attempt})"
        )

    payload = ActionItemStalePayload(
        action_item_id=item.id,
        owner_agent_id=item.owner_agent_id,
        topic_id=item.topic_id,
        decision_id=item.decision_id,
        linked_experiment_id=item.linked_experiment_id,
        last_woken_at=item.last_woken_at,
        wake_count=item.wake_count,
        stale_after_attempt=stale_after_attempt,
        admin_notified=admin_notified,
        creator_audit_only=creator_audit_only,
    )
    return _log_no_commit(
        db,
        action=ACTION_ITEM_STALE,
        target_type="topic_action_item",
        agent_id=item.owner_agent_id,
        project_id=item.project_id,
        target_id=item.id,
        summary=f"行动项「{item.title}」进入 stale 路径（第 {stale_after_attempt} 次唤醒后无响应）",
        payload=payload.model_dump(mode="json"),
    )


def log_action_item_stale(
    db: Session,
    *,
    item: TopicActionItem,
    stale_after_attempt: int,
    admin_notified: bool,
    creator_audit_only: bool,
) -> AuditLog:
    """Write the ``action_item.stale`` audit event after the 4th unanswered wake.

    The runtime-waker stops waking the assignee once ``stale_at`` is set on the
    item; this event is the diagnostic signal that the open item has not
    progressed through T+24h → T+72h → every 7d up to 4 times. See plan §1.

    Payload is validated against ``ActionItemStalePayload`` before being
    written so the audit log stays grep-friendly (B-11 acceptance: payload
    carries ``wake_count`` so reviewers can confirm "走了 stale 路径" without
    joining other tables).

    Autocommit variant — prefer ``_log_action_item_stale_no_commit`` from
    inside a larger service transaction (see ``action_item_service.mark_stale_no_commit``).
    """
    entry = _log_action_item_stale_no_commit(
        db,
        item=item,
        stale_after_attempt=stale_after_attempt,
        admin_notified=admin_notified,
        creator_audit_only=creator_audit_only,
    )
    db.commit()
    db.refresh(entry)
    return entry


def log_action_item_wake_sent(
    db: Session,
    *,
    item: TopicActionItem,
    now: datetime,
) -> AuditLog:
    """Write the ``action_item.wake_sent`` audit row for one wake cycle.

    Audit-only signal — the durable side-effect (``wake_count += 1``,
    ``last_woken_at = now``) is applied by ``action_item_service.mark_wake_sent``.
    Centralising the audit shape here keeps the payload contract in one place.

    The audit row also gets ``wake_count`` after the increment so reviewers can
    reconstruct "the Nth wake" without joining the action_item table.
    """
    return _log_no_commit(
        db,
        action=ACTION_ITEM_WAKE_SENT,
        target_type="topic_action_item",
        agent_id=item.owner_agent_id,
        project_id=item.project_id,
        target_id=item.id,
        summary=f"行动项「{item.title}」第 {item.wake_count} 次唤醒",
        payload={
            "action_item_id": str(item.id),
            "owner_agent_id": str(item.owner_agent_id) if item.owner_agent_id else None,
            "topic_id": str(item.topic_id),
            "wake_count": item.wake_count,
            "last_woken_at": item.last_woken_at.isoformat() if item.last_woken_at else None,
        },
    )


# --- Review-item mutation audit events (experiment b95894db I1(e)) ---------

# I1(e): every write that touches a review item (``resolve-item`` / review
# add / review submit / ``reject-result``) emits a ``review_item.mutation``
# audit row so admins can reconstruct the per-item timeline via
# ``map audit list --kind review_item_mutation --experiment <id>``.
#
# ``list`` / ``show`` queries do not write audit rows — they are read-only.
# The audit row carries the 8-field schema defined in the plan in
# ``payload_json`` so the existing ``AuditLog`` table does not need a
# migration:
#
#   timestamp         (audit row created_at)
#   actor_agent_id    (audit row agent_id)
#   experiment_id     (review_item.review.experiment_id, denormalised for admin filtering)
#   review_item_id    (audit row target_id when target_type=="review_item";
#                      None for experiment-level mutations like reject-result)
#   action            ("resolve_item" / "add_item" / "submit_review" /
#                      "reject_result" — distinguishes the verb)
#   before_state      (legacy ReviewItemStatus.value, or None on add)
#   after_state       (new ReviewItemStatus.value after normalisation, or
#                      "experiment.rejected" for reject-result)
#   reason            (free-form caller-supplied reason — reject_result
#                      summary, or "host rebutted item" / etc.)
REVIEW_ITEM_MUTATION = "review_item.mutation"
_REVIEW_ITEM_MUTATION_TARGET_TYPE = "review_item"


# 0db51e10 I2(5e): cross-persona acceptance_status compare audit.
# 沿用现有 audit_logs 表 + metadata_json 字段，新增 action enum
# ``cross_persona_call``。6 字段写入 payload_json:
#   caller_agent_id      (调用者 agent)
#   target_experiment_id (目标实验)
#   visibility_diff      (per-persona view diff dict)
#   result_partition_count (返回的 persona 数)
#   diff_size             (differing 字段数)
#   timestamp             (audit row created_at 列已自带)
CROSS_PERSONA_CALL = "cross_persona_call"
_CROSS_PERSONA_CALL_TARGET_TYPE = "experiment"


def log_cross_persona_call_no_commit(
    db: Session,
    *,
    caller_agent_id: uuid.UUID,
    target_experiment_id: uuid.UUID,
    project_id: uuid.UUID,
    visibility_diff: dict | None = None,
    result_partition_count: int = 0,
    diff_size: int = 0,
) -> AuditLog:
    """Write the ``cross_persona_call`` audit row without committing.

    Caller owns the transaction (mirrors the review_item.mutation
    pattern). The endpoint that wraps this helper commits.

    Payload shape matches plan 5e contract: ``caller_agent_id`` /
    ``target_experiment_id`` / ``visibility_diff`` /
    ``result_partition_count`` / ``diff_size``. ``timestamp`` is
    surfaced via the audit row's ``created_at`` column (no need to
    duplicate it in payload_json).
    """
    payload = {
        "caller_agent_id": str(caller_agent_id),
        "target_experiment_id": str(target_experiment_id),
        "visibility_diff": visibility_diff or {},
        "result_partition_count": result_partition_count,
        "diff_size": diff_size,
    }
    return _log_no_commit(
        db,
        action=CROSS_PERSONA_CALL,
        target_type=_CROSS_PERSONA_CALL_TARGET_TYPE,
        agent_id=caller_agent_id,
        project_id=project_id,
        target_id=target_experiment_id,
        payload=payload,
    )


def log_review_item_mutation_no_commit(
    db: Session,
    *,
    item: ReviewItem | None,
    experiment_id: uuid.UUID,
    actor_id: uuid.UUID,
    project_id: uuid.UUID,
    action: str,
    before_state: str | None,
    after_state: str | None,
    reason: str | None = None,
    target_id: uuid.UUID | None = None,
) -> AuditLog:
    """Write the ``review_item.mutation`` audit row without committing.

    Caller owns the transaction (mirrors the action_item audit pattern).
    ``item`` may be ``None`` for experiment-level mutations like
    ``reject-result`` — pass ``target_id=experiment_id`` explicitly.
    """
    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "actor_agent_id": str(actor_id),
        "experiment_id": str(experiment_id),
        "review_item_id": str(item.id) if item is not None else None,
        "action": action,
        "before_state": before_state,
        "after_state": after_state,
        "reason": reason,
    }
    return _log_no_commit(
        db,
        action=REVIEW_ITEM_MUTATION,
        target_type=_REVIEW_ITEM_MUTATION_TARGET_TYPE,
        agent_id=actor_id,
        project_id=project_id,
        target_id=target_id if target_id is not None else (
            item.id if item is not None else experiment_id
        ),
        summary=(
            f"review_item {action}: {before_state} -> {after_state}"
            if item is not None
            else f"review_item {action}: experiment {experiment_id}"
        ),
        payload=payload,
    )


# --- Log similarity force-skip audit events (b72d0542 I1.b(2)(e)(g)) ------

# I1.b(2)(e): when the soft similarity check fires AND the caller passes
# ``force_skip_similarity=True``, the warning is suppressed in the log
# create response and a ``log.force_skip`` audit row is written instead.
# Admin queries use ``map audit list --kind log.force_skip --experiment <id>``
# to inspect the timeline.
#
# Payload contract (5 fields, mirrors plan (e)):
#   log_id              (the new log that triggered the warning)
#   ref_log_id          (the prior log the score was computed against)
#   similarity_score    (cosine-equivalent, in [0.0, 1.0])
#   threshold           (warn threshold at the time of the skip)
#   embedding_model     (model id used for the score)
LOG_FORCE_SKIP = "log.force_skip"
_LOG_FORCE_SKIP_TARGET_TYPE = "experiment_log"


def log_force_skip_no_commit(
    db: Session,
    *,
    log_id: uuid.UUID,
    ref_log_id: uuid.UUID,
    experiment_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    similarity_score: float,
    threshold: float,
    embedding_model: str,
) -> AuditLog:
    """Write the ``log.force_skip`` audit row without committing.

    Caller owns the transaction (mirrors the cross_persona_call pattern).
    The endpoint that wraps this helper commits so the log row and the
    audit row land atomically — see
    ``server/api/experiments.py::create_log``.
    """
    payload = {
        "log_id": str(log_id),
        "ref_log_id": str(ref_log_id),
        "experiment_id": str(experiment_id),
        "similarity_score": similarity_score,
        "threshold": threshold,
        "embedding_model": embedding_model,
    }
    return _log_no_commit(
        db,
        action=LOG_FORCE_SKIP,
        target_type=_LOG_FORCE_SKIP_TARGET_TYPE,
        agent_id=actor_id,
        project_id=project_id,
        target_id=log_id,
        summary=(
            f"log.force_skip: similarity {similarity_score:.2f} "
            f">= threshold {threshold:.2f} (model={embedding_model})"
        ),
        payload=payload,
    )
