"""STATE_MACHINE.* error registry + recovery hint helper (experiment 156172e9 I1(a)).

The MAP state machine raises :class:`server.services.errors.StateTransitionError`
on illegal transitions (phase / review-item / topic-status). The exception
carries an optional ``error_code`` string (e.g. ``REVIEW_REJECT_RESULT_MISUSE``)
that callers can branch on programmatically instead of pattern-matching the
human-readable ``detail`` text.

This module:

1. Defines the canonical ``STATE_MACHINE_*`` error code constants so callers
   can ``from map_client.errors import STATE_MACHINE_REJECT_RESULT_MISUSE``
   instead of typo-prone string literals.
2. Maintains a :data:`RECOVERY_HINTS` registry mapping each code to a
   short, actionable recovery hint (one canonical line that says "try X" or
   "see Y").
3. Exposes :func:`recovery_hint` so SDK callers — and the CLI ``_run``
   handler — can look up the hint by error code (or by passing the
   ``MAPHTTPError`` instance directly).

The hint registry is the **single source of truth** for the SDK-side
recovery narrative; the server-side ``StateTransitionError.hint`` field is
still authoritative for HTTP errors. The SDK helper only kicks in when:

- the caller wants a hint for a known STATE_MACHINE.* code without making
  an HTTP call (e.g. unit tests, scripted pre-flight, agent planning); or
- the server did not include a ``hint`` field in the response (older API
  versions / legacy fallbacks).

Fixtures live in ``tests/fixtures/state_machine_errors.yaml`` and are
loaded by tests; the registry in this module is the canonical Python
mapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from map_client.exceptions import MAPHTTPError


# --- STATE_MACHINE.* error code constants ---------------------------------
#
# Naming convention: ``STATE_MACHINE_<DOMAIN>_<REASON>``. Each constant
# corresponds to one server-side ``StateTransitionError(error_code=...)``
# call site; the test suite asserts every constant has a non-empty hint.
#
# When the server adds a new code, add the constant here + a hint in
# RECOVERY_HINTS in the same commit; do not let the constants drift.

# Phase machine: experiment / topic phase transitions.
STATE_MACHINE_INVALID_PHASE_TRANSITION = "STATE_MACHINE_INVALID_PHASE_TRANSITION"
STATE_MACHINE_TERMINAL_PHASE = "STATE_MACHINE_TERMINAL_PHASE"
STATE_MACHINE_PLAN_REQUIRED = "STATE_MACHINE_PLAN_REQUIRED"
STATE_MACHINE_MISSING_EVIDENCE = "STATE_MACHINE_MISSING_EVIDENCE"
STATE_MACHINE_REVISE_OUT_OF_PHASE = "STATE_MACHINE_REVISE_OUT_OF_PHASE"
STATE_MACHINE_LOG_OUT_OF_PHASE = "STATE_MACHINE_LOG_OUT_OF_PHASE"
STATE_MACHINE_TOPIC_INVALID_TRANSITION = "STATE_MACHINE_TOPIC_INVALID_TRANSITION"
STATE_MACHINE_ACCEPTANCE_UNKNOWN_TYPE = "STATE_MACHINE_ACCEPTANCE_UNKNOWN_TYPE"

# Review-item machine: per-item status transitions.
STATE_MACHINE_REVIEW_ITEM_INVALID_TRANSITION = "STATE_MACHINE_REVIEW_ITEM_INVALID_TRANSITION"
STATE_MACHINE_REVIEW_ITEM_ACTOR_DENIED = "STATE_MACHINE_REVIEW_ITEM_ACTOR_DENIED"
STATE_MACHINE_REVIEW_ITEM_NOT_UNREASONABLE = "STATE_MACHINE_REVIEW_ITEM_NOT_UNREASONABLE"
STATE_MACHINE_REVIEW_ITEM_NO_STATUS = "STATE_MACHINE_REVIEW_ITEM_NO_STATUS"
STATE_MACHINE_REVIEW_ITEM_REBUT_AFTER_REVIEW = "STATE_MACHINE_REVIEW_ITEM_REBUT_AFTER_REVIEW"

# Review aggregate: review-submission / withdraw gates.
STATE_MACHINE_REVIEW_NOT_IN_REVIEW_PHASE = "STATE_MACHINE_REVIEW_NOT_IN_REVIEW_PHASE"
STATE_MACHINE_REVIEW_WITHDRAW_NOT_IN_REVIEW_PHASE = (
    "STATE_MACHINE_REVIEW_WITHDRAW_NOT_IN_REVIEW_PHASE"
)

# Specific misuse subcode (carried forward from I1(d) — the first
# STATE_MACHINE.* code with a server-side hint).
STATE_MACHINE_REJECT_RESULT_MISUSE = "REVIEW_REJECT_RESULT_MISUSE"

# I1(d) — archived review cannot be mutated. Triggered by
# update_review_item / withdraw_review when the parent review row
# carries archived_at != NULL (set by plan_revise auto-archive, manual
# admin archive, or the migration 034 backfill marker).
STATE_MACHINE_REVIEW_ALREADY_ARCHIVED = "REVIEW_ALREADY_ARCHIVED"


# Default error code used by server-side ``StateTransitionError`` when no
# explicit ``error_code`` is supplied (legacy / pre-I1(d) call sites).
STATE_MACHINE_GENERIC = "state_machine_error"


# All known STATE_MACHINE.* error codes. Used by ``test_recovery_hint_*``
# to assert every code has a hint. Keep in sync with the constants above
# + any new ones; the test will fail loudly if you forget.
STATE_MACHINE_ERROR_CODES: frozenset[str] = frozenset(
    {
        # Phase
        STATE_MACHINE_INVALID_PHASE_TRANSITION,
        STATE_MACHINE_TERMINAL_PHASE,
        STATE_MACHINE_PLAN_REQUIRED,
        STATE_MACHINE_MISSING_EVIDENCE,
        STATE_MACHINE_REVISE_OUT_OF_PHASE,
        STATE_MACHINE_LOG_OUT_OF_PHASE,
        STATE_MACHINE_TOPIC_INVALID_TRANSITION,
        STATE_MACHINE_ACCEPTANCE_UNKNOWN_TYPE,
        # Review item
        STATE_MACHINE_REVIEW_ITEM_INVALID_TRANSITION,
        STATE_MACHINE_REVIEW_ITEM_ACTOR_DENIED,
        STATE_MACHINE_REVIEW_ITEM_NOT_UNREASONABLE,
        STATE_MACHINE_REVIEW_ITEM_NO_STATUS,
        STATE_MACHINE_REVIEW_ITEM_REBUT_AFTER_REVIEW,
        # Review aggregate
        STATE_MACHINE_REVIEW_NOT_IN_REVIEW_PHASE,
        STATE_MACHINE_REVIEW_WITHDRAW_NOT_IN_REVIEW_PHASE,
        # Specific misuse subcode
        STATE_MACHINE_REJECT_RESULT_MISUSE,
        # I1(d) — archived review refusal
        STATE_MACHINE_REVIEW_ALREADY_ARCHIVED,
        # Generic
        STATE_MACHINE_GENERIC,
    }
)


# Hint registry: code → recovery hint (one canonical actionable line).
#
# Conventions:
# - One short sentence per code (≤ 200 chars).
# - Lead with the action ("Run X", "Use Y", "Wait for Z"); avoid generic
#   "Contact admin".
# - Reference the canonical CLI command when one exists.
# - When no command exists, point to the docs path.
#
# Tests assert that every value is a non-empty string. Empty / ``None``
# hints fail ``test_recovery_hint_covers_all_state_machine_codes``.
RECOVERY_HINTS: dict[str, str] = {
    STATE_MACHINE_INVALID_PHASE_TRANSITION: (
        "实验当前 phase 不允许此操作；运行 `map --persona host experiment "
        "show --id <uuid>` 查看 current_phase + allowed transitions。"
    ),
    STATE_MACHINE_TERMINAL_PHASE: (
        "实验已进入终态 (done / cancelled)；变更实验需创建新实验。"
    ),
    STATE_MACHINE_PLAN_REQUIRED: (
        "实验需要至少 1 个 plan 版本；先 `map --persona host experiment "
        "plan revise --id <uuid> --plan-file <path>` 再 submit-review。"
    ),
    STATE_MACHINE_MISSING_EVIDENCE: (
        "complete 需要 evidence metadata (pytest_summary / alembic_current / "
        "api_health / image_digest 至少一项)；参考 `map --persona host "
        "experiment pre-complete --help`。非部署型实验用 "
        "`--allow-missing-evidence`。"
    ),
    STATE_MACHINE_REVISE_OUT_OF_PHASE: (
        "plan 只能在 draft / review / running 阶段修订；`map --persona host "
        "experiment status --id <uuid>` 查看 phase。"
    ),
    STATE_MACHINE_LOG_OUT_OF_PHASE: (
        "log 只能在 running / result_review / done 阶段追加；先 `start` 实验 "
        "或等待 reviewer accept-result。"
    ),
    STATE_MACHINE_TOPIC_INVALID_TRANSITION: (
        "话题状态机拒绝此转换；`map --persona host topic show --id <uuid>` "
        "查看 allowed transitions（如 open↔closed 不能跨 archive）。"
    ),
    STATE_MACHINE_ACCEPTANCE_UNKNOWN_TYPE: (
        "acceptance_type 必须是 migration / smoke / unit_test / integration / "
        "manual 之一；详见 `map docs error-codes --search acceptance`。"
    ),
    STATE_MACHINE_REVIEW_ITEM_INVALID_TRANSITION: (
        "review item 状态机拒绝此转换；`map --persona host experiment review "
        "list --id <uuid>` 查看当前 status + allowed transitions。"
    ),
    STATE_MACHINE_REVIEW_ITEM_ACTOR_DENIED: (
        "当前 persona 无权做此 item 转换；reviewer 转换需 is_reviewer，"
        "host 转换需 is_creator。换 persona 或请 admin 介入。"
    ),
    STATE_MACHINE_REVIEW_ITEM_NOT_UNREASONABLE: (
        "只有 unreasonable item 有 mutable status；reasonable item 是只读 "
        "observation，调用方不需要 resolve。"
    ),
    STATE_MACHINE_REVIEW_ITEM_NO_STATUS: (
        "review item 当前没有 status（通常是数据迁移遗留）；"
        "`map --persona reviewer experiment review add` 重新提交 review。"
    ),
    STATE_MACHINE_REVIEW_ITEM_REBUT_AFTER_REVIEW: (
        "实验已过 review 阶段，单 item rebut 不再生效；若要驳回整个 result，"
        "让 reviewer / admin 调用 `map --persona reviewer experiment "
        "reject-result --id <uuid>`。"
    ),
    STATE_MACHINE_REVIEW_NOT_IN_REVIEW_PHASE: (
        "review 只能在 review phase 提交；`map --persona host experiment "
        "show --id <uuid>` 确认 phase=review。"
    ),
    STATE_MACHINE_REVIEW_WITHDRAW_NOT_IN_REVIEW_PHASE: (
        "withdraw_review 只能在 review phase 执行；实验已进入下游 phase，"
        "review 不能再撤。"
    ),
    STATE_MACHINE_REJECT_RESULT_MISUSE: (
        "单 item 驳回请用 resolve-item --status rebutted (review 阶段); "
        "整个实验驳回请让 reviewer / admin 调用 reject-result, "
        "host creator 不可拒绝自己的 result。"
    ),
    STATE_MACHINE_REVIEW_ALREADY_ARCHIVED: (
        "查看 `map experiment review list --include-archived --plan-version <v>` 历史; "
        "若需要修改 item, 请在新的 plan_version 提交新 review。"
    ),
    STATE_MACHINE_GENERIC: (
        "状态机拒绝此操作；详见 `map docs error-codes` 或 `map --persona "
        "host experiment show --id <uuid>`。"
    ),
}


# Default generic hint for codes outside the registry. Used when
# ``recovery_hint`` is called with an unknown code (forward compatibility
# for codes the SDK does not yet know about).
_GENERIC_FALLBACK_HINT = (
    "未识别的 state_machine_error_code；查看 `map docs error-codes` 列表，"
    "或升级 SDK 到最新版本以获得专属 hint。"
)


def recovery_hint(
    exc_or_code: MAPHTTPError | str | None,
) -> str:
    """Return the recovery hint for a STATE_MACHINE.* error.

    Accepts either:

    - a :class:`MAPHTTPError` instance (reads its ``error_code`` attribute);
    - a raw error code string;
    - ``None`` (returns the generic fallback).

    Lookup order:

    1. If the value carries an ``error_code`` attribute (or is a string),
       use it as the lookup key.
    2. Look up the key in :data:`RECOVERY_HINTS`.
    3. If the key is not in the registry, return the generic fallback so
       callers never see a ``KeyError`` / ``None`` from this helper.

    The function never raises — callers can use it as the single entry
    point in error handlers without ``try``/``except``.
    """
    if exc_or_code is None:
        return _GENERIC_FALLBACK_HINT
    code = getattr(exc_or_code, "error_code", None)
    if code is None and isinstance(exc_or_code, str):
        code = exc_or_code
    if not code:
        return _GENERIC_FALLBACK_HINT
    return RECOVERY_HINTS.get(code, _GENERIC_FALLBACK_HINT)


__all__ = [
    "STATE_MACHINE_ERROR_CODES",
    "RECOVERY_HINTS",
    "recovery_hint",
    # Phase
    "STATE_MACHINE_INVALID_PHASE_TRANSITION",
    "STATE_MACHINE_TERMINAL_PHASE",
    "STATE_MACHINE_PLAN_REQUIRED",
    "STATE_MACHINE_MISSING_EVIDENCE",
    "STATE_MACHINE_REVISE_OUT_OF_PHASE",
    "STATE_MACHINE_LOG_OUT_OF_PHASE",
    "STATE_MACHINE_TOPIC_INVALID_TRANSITION",
    "STATE_MACHINE_ACCEPTANCE_UNKNOWN_TYPE",
    # Review item
    "STATE_MACHINE_REVIEW_ITEM_INVALID_TRANSITION",
    "STATE_MACHINE_REVIEW_ITEM_ACTOR_DENIED",
    "STATE_MACHINE_REVIEW_ITEM_NOT_UNREASONABLE",
    "STATE_MACHINE_REVIEW_ITEM_NO_STATUS",
    "STATE_MACHINE_REVIEW_ITEM_REBUT_AFTER_REVIEW",
    # Review aggregate
    "STATE_MACHINE_REVIEW_NOT_IN_REVIEW_PHASE",
    "STATE_MACHINE_REVIEW_WITHDRAW_NOT_IN_REVIEW_PHASE",
    # Misuse subcode
    "STATE_MACHINE_REJECT_RESULT_MISUSE",
    # I1(d) archived review refusal
    "STATE_MACHINE_REVIEW_ALREADY_ARCHIVED",
    # Generic
    "STATE_MACHINE_GENERIC",
]
