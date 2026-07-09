"""cli-ux PR3: ReviewVerdictItem accepts accept|reject|dismiss aliases.

Verifies three contracts:

1. Aliases map to canonical ``ReviewVerdict`` enum values:
   ``accept`` → ``passed``, ``reject`` → ``failed``, ``dismiss`` → ``waived``.
2. Serialized output is ALWAYS the canonical value
   (``passed``/``failed``/``waived``) — DB rows and downstream JSON consumers
   are unaffected.
3. ``dismiss`` triggers the existing ``waived`` requires-reason validation
   (so the alias is not a loophole to skip the rationale requirement).
"""
from __future__ import annotations

import uuid

import pytest
from map_types.enums import ReviewVerdict
from map_types.schemas import ReviewVerdictItem
from pydantic import ValidationError


def _item_id() -> uuid.UUID:
    return uuid.uuid4()


# --- accepted input forms ---------------------------------------------------


_WAIVED_OK_REASON = (
    "this is a fully formed rationale that is at least fifty characters long. ok."
)


@pytest.mark.parametrize(
    "input_verdict, expected",
    [
        # canonical (existing behavior)
        ("passed", ReviewVerdict.passed),
        ("failed", ReviewVerdict.failed),
        ("waived", ReviewVerdict.waived),
        # aliases (cli-ux PR3)
        ("accept", ReviewVerdict.passed),
        ("reject", ReviewVerdict.failed),
        ("dismiss", ReviewVerdict.waived),
        # case-insensitive + whitespace tolerant
        ("ACCEPT", ReviewVerdict.passed),
        ("  Reject  ", ReviewVerdict.failed),
        ("DISMISS", ReviewVerdict.waived),
    ],
)
def test_aliases_map_to_canonical_enum(input_verdict: str, expected: ReviewVerdict) -> None:
    item = ReviewVerdictItem(
        item_id=_item_id(),
        verdict=input_verdict,  # type: ignore[arg-type]
        reason=_WAIVED_OK_REASON if expected == ReviewVerdict.waived else None,
    )
    assert item.verdict == expected, (
        f"input {input_verdict!r} should map to {expected.value!r}, got {item.verdict.value!r}"
    )


# --- serialization stability ------------------------------------------------


@pytest.mark.parametrize(
    "input_verdict",
    ["accept", "passed", "reject", "failed", "dismiss", "waived", "ACCEPT"],
)
def test_serialized_value_is_always_canonical(input_verdict: str) -> None:
    """No matter what alias you provide, model_dump outputs canonical value."""
    item = ReviewVerdictItem(
        item_id=_item_id(),
        verdict=input_verdict,  # type: ignore[arg-type]
        reason=_WAIVED_OK_REASON if input_verdict.lower() in {"waived", "dismiss"} else None,
    )
    dumped = item.model_dump()
    assert dumped["verdict"] in {"passed", "failed", "waived"}, (
        f"verdict serialized as {dumped['verdict']!r}; must be one of "
        f"the canonical 3 values"
    )


def test_serialized_json_uses_canonical_value() -> None:
    """JSON round-trip also stays canonical (not alias)."""
    item = ReviewVerdictItem(item_id=_item_id(), verdict="accept")  # type: ignore[arg-type]
    j = item.model_dump_json()
    assert '"verdict":"passed"' in j
    assert "accept" not in j


# --- waived reason requirement applies to dismiss ---------------------------


def test_dismiss_alias_still_requires_reason() -> None:
    """``dismiss`` maps to ``waived``, which requires a non-empty reason."""
    with pytest.raises(ValidationError) as exc_info:
        ReviewVerdictItem(item_id=_item_id(), verdict="dismiss")  # type: ignore[arg-type]
    assert "waived" in str(exc_info.value).lower()


def test_dismiss_alias_with_reason_passes() -> None:
    item = ReviewVerdictItem(
        item_id=_item_id(),
        verdict="dismiss",  # type: ignore[arg-type]
        reason=_WAIVED_OK_REASON,
    )
    assert item.verdict == ReviewVerdict.waived
    assert item.reason is not None


# --- rejected input ---------------------------------------------------------


@pytest.mark.parametrize("invalid", ["approve", "yes", "no", "maybe", "", "p"])
def test_invalid_verdict_strings_rejected(invalid: str) -> None:
    """Unknown verdict strings (not in enum AND not in alias map) raise."""
    with pytest.raises(ValidationError):
        ReviewVerdictItem(
            item_id=_item_id(),
            verdict=invalid,  # type: ignore[arg-type]
            reason=(
                _WAIVED_OK_REASON if invalid in {"dismiss", "waived"} else None
            ),
        )
