"""Action-item escalation timeline (experiment B, plan §3 / I4).

纯决策模块：根据 action_item 的 ``first_open_at`` / ``last_woken_at`` /
``wake_count`` / ``stale_at`` 字段判断当前时刻应该 WAKE / STALE / SKIP。
无 I/O，无时钟，``now`` 由调用方注入以便单元测试钉住时间。

阈值常量镜像 ``server.services.action_item_service``（
``WAKE_STAGE_THRESHOLDS`` / ``WAKE_REPEAT_INTERVAL_DAYS`` /
``WAKE_MAX_COUNT_BEFORE_STALE``）。两侧需保持一致——
``test_threshold_constants_match_plan_section_three`` 是这条不变式的 lint。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any


class ActionItemWakeDecision(str, Enum):
    """Outcome of ``should_wake_action_item`` for one action_item at ``now``."""

    WAKE = "wake"
    STALE = "stale"
    SKIP = "skip"


# Mirrored from server.services.action_item_service. Importing would force
# the waker CLI to depend on the server-side stack (SQLAlchemy models +
# FastAPI deps), which violates the waker's no-FastAPI invariant. Kept
# short and obvious so a reviewer can spot drift.
_WAKE_STAGE_HOURS: tuple[tuple[int, int], ...] = (
    (1, 24),   # wake_count_after_increment=1 → first wake at T+24h
    (2, 72),   # wake_count_after_increment=2 → second wake at T+72h
)
_WAKE_REPEAT_DAYS = 7  # after the 72h wake, fire every 7d
_WAKE_MAX_BEFORE_STALE = 4  # 4th unanswered wake → stale


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into an aware UTC datetime.

    Defensive about the variety of shapes the SDK / API may emit
    (``...Z`` vs ``...+00:00`` vs naive ISO). Returns ``None`` if the value
    is missing or unparseable — callers treat ``None`` as "skip".
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def should_wake_action_item(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ActionItemWakeDecision:
    """Decide the escalation action for one open action_item at ``now``.

    Pure function (no I/O, no clock). Returns ``WAKE`` when the waker
    should bump ``wake_count`` and emit a wake event, ``STALE`` when the
    4th wake has already fired and the assignee still hasn't responded
    (we mark stale + stop waking), or ``SKIP`` when the escalation
    timeline does not yet call for an action.

    Rules (plan §3, mirrored from ``server.services.action_item_service``):

    - Only open items are eligible; closed items (done / cancelled) and
      unassigned items (no ``owner_agent_id``) are skipped.
    - Stale items (``stale_at`` set) are skipped forever — once we've
      written the diagnostic audit row we stop bothering the assignee.
    - First wake fires 24h after ``first_open_at``; second at 72h; further
      wakes every 7d; the (N+1)th check after the 4th wake writes stale.
    - ``now`` defaults to ``datetime.now(UTC)`` — callers that need a
      deterministic clock (tests, dogfood) inject their own.
    """
    if not isinstance(item, dict):
        return ActionItemWakeDecision.SKIP
    if item.get("status") != "open":
        return ActionItemWakeDecision.SKIP
    if not item.get("owner_agent_id"):
        # plan §3 「仅 assignee」 — unassigned items must not be woken by
        # anyone, the waker has no addressee.
        return ActionItemWakeDecision.SKIP
    if _parse_iso_datetime(item.get("stale_at")) is not None:
        return ActionItemWakeDecision.SKIP

    current = now or datetime.now(UTC)
    first_open_at = _parse_iso_datetime(item.get("first_open_at"))
    if first_open_at is None:
        # Pre-I1 backfill may have missed this row (closed before I1, etc).
        # Conservatively skip — re-running the backfill is the remediation.
        return ActionItemWakeDecision.SKIP
    last_woken_at = _parse_iso_datetime(item.get("last_woken_at"))
    wake_count = int(item.get("wake_count") or 0)

    elapsed = current - first_open_at

    # Stage 1 & 2: hard thresholds keyed to first_open_at.
    for target_count, min_hours in _WAKE_STAGE_HOURS:
        if wake_count + 1 == target_count and elapsed >= timedelta(hours=min_hours):
            return ActionItemWakeDecision.WAKE

    # Stages 3+: every 7d after the last wake.
    if 2 <= wake_count < _WAKE_MAX_BEFORE_STALE and last_woken_at is not None:
        if current - last_woken_at >= timedelta(days=_WAKE_REPEAT_DAYS):
            return ActionItemWakeDecision.WAKE

    # Past the 4th unanswered wake: write stale.
    if wake_count >= _WAKE_MAX_BEFORE_STALE and last_woken_at is not None:
        if current - last_woken_at >= timedelta(days=_WAKE_REPEAT_DAYS):
            return ActionItemWakeDecision.STALE

    return ActionItemWakeDecision.SKIP


def scan_pending_action_items(
    action_items: list[dict[str, Any]],
    *,
    persona_agent_id: str | None,
    now: datetime | None = None,
) -> list[tuple[str, ActionItemWakeDecision]]:
    """Filter the ``action_items`` todo payload through ``should_wake_action_item``.

    Returns a list of ``(action_item_id, decision)`` pairs. Only items
    owned by ``persona_agent_id`` are considered — the waker must never
    wake another agent's action_item even if it shows up in the
    cross-project listing. Items whose owner doesn't match are skipped
    silently (not raised) — the per-persona todos feed already scopes
    this but we re-check defensively against potential payload leakage.
    """
    decisions: list[tuple[str, ActionItemWakeDecision]] = []
    for item in action_items or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if persona_agent_id and str(item.get("owner_agent_id") or "") != str(persona_agent_id):
            continue
        decisions.append((item_id, should_wake_action_item(item, now=now)))
    return decisions


@dataclass(frozen=True)
class EscalationDecisionCounts:
    """Aggregated counts of WAKE / STALE / SKIP for one scan."""

    wake: int = 0
    stale: int = 0
    skip: int = 0

    @classmethod
    def from_decisions(
        cls, decisions: list[tuple[str, ActionItemWakeDecision]]
    ) -> "EscalationDecisionCounts":
        wake = stale = skip = 0
        for _, decision in decisions:
            if decision is ActionItemWakeDecision.WAKE:
                wake += 1
            elif decision is ActionItemWakeDecision.STALE:
                stale += 1
            else:
                skip += 1
        return cls(wake=wake, stale=stale, skip=skip)
