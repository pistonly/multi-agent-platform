"""STATE_MACHINE.* recovery_hint() helper coverage (experiment 156172e9 I1(a)).

Pins plan (a) acceptance:

- ``sdk.map_client.errors.recovery_hint(exc_or_code) -> str`` returns a
  non-empty hint string for every STATE_MACHINE.* code in
  ``STATE_MACHINE_ERROR_CODES``.
- The helper accepts either a ``MAPHTTPError`` instance (reads
  ``error_code``) or a raw code string.
- Unknown codes fall back to the generic hint (never raise / return None).
- The YAML fixture (``tests/fixtures/state_machine_errors.yaml``) is the
  cross-check that the SDK registry agrees with the server-side code
  catalogue: every fixture entry maps to a registered code, and every
  registered code has a fixture entry.
- The CLI surface (TODO in I1(c)) gets the same hint via
  ``MAPHTTPError(error_code=...)`` propagation — verified here on the SDK
  side so I1(c) can wire it through without re-asserting the registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from map_client import MAPHTTPError
from map_client.errors import (
    RECOVERY_HINTS,
    STATE_MACHINE_ERROR_CODES,
    STATE_MACHINE_GENERIC,
    STATE_MACHINE_REJECT_RESULT_MISUSE,
    recovery_hint,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "state_machine_errors.yaml"
)


def _load_fixtures() -> list[dict]:
    """Load the YAML fixture as a list of {code, status_code, ...} dicts."""
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_recovery_hint_covers_all_state_machine_codes() -> None:
    """Every code in the registry has a non-empty hint in RECOVERY_HINTS."""
    missing = [c for c in STATE_MACHINE_ERROR_CODES if not RECOVERY_HINTS.get(c)]
    assert not missing, (
        f"RECOVERY_HINTS missing non-empty entries for: {missing}. "
        "Add the constant + hint in sdk/python/map_client/errors.py together."
    )


def test_recovery_hint_returns_string_for_each_registry_code() -> None:
    """recovery_hint(code) returns the registry value for every code."""
    for code in STATE_MACHINE_ERROR_CODES:
        hint = recovery_hint(code)
        assert isinstance(hint, str)
        assert hint.strip(), f"empty hint for code={code}"


def test_recovery_hint_accepts_maphttperror() -> None:
    """recovery_hint(exc) reads error_code attribute from MAPHTTPError."""
    exc = MAPHTTPError(
        status_code=422,
        detail="host creator cannot reject own result",
        error_code=STATE_MACHINE_REJECT_RESULT_MISUSE,
        hint="server-side hint preserved when present",
        retryable=False,
    )
    hint = recovery_hint(exc)
    assert hint == RECOVERY_HINTS[STATE_MACHINE_REJECT_RESULT_MISUSE]


def test_recovery_hint_accepts_raw_string() -> None:
    """recovery_hint("CODE") works without wrapping in MAPHTTPError."""
    assert (
        recovery_hint(STATE_MACHINE_REJECT_RESULT_MISUSE)
        == RECOVERY_HINTS[STATE_MACHINE_REJECT_RESULT_MISUSE]
    )


def test_recovery_hint_unknown_code_falls_back() -> None:
    """Unknown code returns the generic fallback (never raises / None)."""
    hint = recovery_hint("STATE_MACHINE_NOT_REAL_CODE_FUTURE")
    assert isinstance(hint, str)
    assert hint.strip()


def test_recovery_hint_none_returns_generic_fallback() -> None:
    """recovery_hint(None) returns the generic fallback string."""
    assert recovery_hint(None) == recovery_hint("__definitely_unknown__")


def test_recovery_hint_string_without_error_code_attr() -> None:
    """A bare object (no error_code attr) is treated as None / fallback."""
    class _Bag:
        pass

    assert recovery_hint(_Bag()) == recovery_hint(None)


def test_fixtures_cover_all_registry_codes() -> None:
    """YAML fixture has an entry for every STATE_MACHINE_ERROR_CODES code."""
    fixtures = _load_fixtures()
    fixture_codes = {row["code"] for row in fixtures}
    missing_in_fixture = STATE_MACHINE_ERROR_CODES - fixture_codes
    assert not missing_in_fixture, (
        f"tests/fixtures/state_machine_errors.yaml missing entries for: "
        f"{sorted(missing_in_fixture)}"
    )


def test_fixtures_only_contain_known_codes() -> None:
    """YAML fixture must not introduce codes the SDK does not know about."""
    fixtures = _load_fixtures()
    fixture_codes = {row["code"] for row in fixtures}
    extra = fixture_codes - STATE_MACHINE_ERROR_CODES
    assert not extra, (
        f"fixture defines codes not in STATE_MACHINE_ERROR_CODES: {sorted(extra)}. "
        "Add the constant + RECOVERY_HINTS entry first, then add the fixture row."
    )


def test_fixtures_status_codes_are_valid() -> None:
    """Each fixture's status_code is one of the MAP error families."""
    fixtures = _load_fixtures()
    valid = {403, 409, 422, 500}
    for row in fixtures:
        assert row["status_code"] in valid, (
            f"fixture code={row['code']} status_code={row['status_code']} not in {valid}"
        )


def test_recovery_hint_legacy_fallback_code() -> None:
    """The pre-I1(d) fallback code 'state_machine_error' has its own hint."""
    hint = recovery_hint(STATE_MACHINE_GENERIC)
    assert hint == RECOVERY_HINTS[STATE_MACHINE_GENERIC]
    assert "状态机" in hint or "experiment show" in hint


@pytest.mark.parametrize("code", sorted(STATE_MACHINE_ERROR_CODES))
def test_recovery_hint_parametrized(code: str) -> None:
    """Parametrised: every STATE_MACHINE.* code resolves to a non-empty hint.

    This is the canonical acceptance assertion from plan (a):
    "N 个 STATE_MACHINE.* 枚举每个至少一条 fixture + hint 字符串非空".
    """
    hint = recovery_hint(code)
    assert isinstance(hint, str)
    assert len(hint) > 0
    # Hint should mention a recovery affordance — not just echo the code.
    assert hint != code, f"hint must add information, not just echo code={code}"
