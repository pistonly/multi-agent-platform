"""Phase → owner resolution (experiment f873c287 I1(b)).

A single source of truth that maps each ``ExperimentPhase`` value to the
persona who currently holds decision authority to *advance* that phase.

This module is intentionally pure (no DB, no side effects) so the
``my_open_experiments.partition`` layer, the UI copy helper, and the
``experiments_needing_attention`` filter can all import the same table
without a circular dependency.

Schema: ``PhaseOwner`` enum lives in ``sdk/python/map_types/enums.py`` and
is the public contract; the string values here MUST match it. Keeping a
local mirror avoids importing the SDK inside Alembic / CLI layers that
should not pull pydantic.

Why per phase? The decision-owner semantics are stable across runs (see
experiment plan v2 I1(b) acceptance), but the resolver makes it easy to
re-route by changing one mapping instead of grepping every consumer.

Caveats
-------
- ``revise`` is not an ``ExperimentPhase`` value (revising happens inside
  the ``review`` phase via ``plan_revise``); we keep it in the table as a
  "logical phase" so callers can ask ``owner_for("revise")`` when
  computing informational_only / UI copy during plan-revise windows.
"""

from __future__ import annotations

from typing import Final

# Reuse the SDK enum so the public contract stays single-source. If the
# enum is ever renamed, this import will surface that immediately at the
# service layer.
from map_types.enums import ExperimentMode, ExperimentPhase, PhaseOwner

# Mirror the enum values verbatim so Alembic / non-SDK callers don't need
# to import pydantic. KEPT IN SYNC with PhaseOwner — assertion below.
_OWNERS: Final[dict[str, str]] = {
    ExperimentPhase.draft.value: PhaseOwner.host.value,
    ExperimentPhase.review.value: PhaseOwner.reviewer.value,
    # "revise" is a logical sub-phase of review; ``plan_revise`` action
    # is a host decision so the host owns advancement.
    "revise": PhaseOwner.host.value,
    ExperimentPhase.approved.value: PhaseOwner.host.value,
    ExperimentPhase.running.value: PhaseOwner.host.value,
    # 实验 bd9b21f6: --breaking-audit 打回的重评相位——reviewer 是重评主人，
    # host 侧 informational（等重评通过自动迁回 running）。
    ExperimentPhase.pending_review.value: PhaseOwner.reviewer.value,
    ExperimentPhase.result_review.value: PhaseOwner.reviewer.value,
    ExperimentPhase.done.value: PhaseOwner.host.value,
    ExperimentPhase.cancelled.value: PhaseOwner.host.value,
}

assert set(_OWNERS) == {*{p.value for p in ExperimentPhase}, "revise"}, (
    f"phase_owner_resolver: phase coverage drifted. "
    f"missing={set(p.value for p in ExperimentPhase) - _OWNERS.keys() - {'revise'}} "
    f"extra={set(_OWNERS) - {p.value for p in ExperimentPhase} - {'revise'}}"
)

# v0.10: in direct mode, the ``running`` phase is owned by the
# participant (executor), not the host. The host is informational_only
# during execution — waiting for the participant to complete.
_DIRECT_OVERRIDES: Final[dict[str, str]] = {
    ExperimentPhase.running.value: PhaseOwner.participant.value,
}


def owner_for(
    phase: str | ExperimentPhase,
    *,
    mode: str = ExperimentMode.standard.value,
) -> PhaseOwner:
    """Return the persona who currently holds decision authority for ``phase``.

    Accepts either an ``ExperimentPhase`` enum member or its raw string
    value. Unknown phases fall back to ``host`` — the conservative default
    that keeps ``informational_only`` false (which means the experiment
    stays in the host's actionable obligation list).

    In ``direct`` mode (v0.10), the ``running`` phase is owned by the
    participant (executor) instead of the host, so todos / waker route
    execution obligations to the participant persona.
    """
    key = phase.value if isinstance(phase, ExperimentPhase) else phase
    if mode == ExperimentMode.direct.value and key in _DIRECT_OVERRIDES:
        return PhaseOwner(_DIRECT_OVERRIDES[key])
    return PhaseOwner(_OWNERS.get(key, PhaseOwner.host.value))


def is_informational_only(
    phase: str | ExperimentPhase,
    *,
    actions: list[str],
    blocked_on: str | None,
    mode: str = ExperimentMode.standard.value,
) -> bool:
    """I1(a) auto-classification predicate.

    Returns True iff all three preconditions hold:

    1. ``actions`` is empty (host has no actionable next step).
    2. ``blocked_on`` is non-empty (a state-machine gate is engaged).
    3. ``phase_owner`` is **not** the host (decision authority is held by
       another persona, so the host can only wait).

    This predicate is the single source of truth for the
    ``my_open_experiments.partition[].informational_only`` field; it is
    re-used by the waker exclusion rule (I1(f)) and by the participant
    filter (I1(e)).

    In ``direct`` mode (v0.10), the ``running`` phase is owned by the
    participant, so the host is informational_only during execution.
    """
    if actions:
        return False
    if not blocked_on:
        return False
    return owner_for(phase, mode=mode) is not PhaseOwner.host
