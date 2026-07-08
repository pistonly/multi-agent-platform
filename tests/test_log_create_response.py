"""8ac93d4e I1.c — LogCreateResponse wrapper end-to-end tests.

Covers:
* API endpoint ``POST /experiments/{id}/logs`` returns the wrapper shape
  ``{log: ExperimentLogRead, validation: EvidenceValidationSchema}``.
* The ``validation`` field carries plan evidence_keys warnings / parse_error
  / plan_keys, but ``valid`` is always True (soft validation — never blocks).
* SDK ``MAPClient.create_log`` returns a ``LogCreateResponse`` that
  ``model_validate`` parses correctly.
* ``EvidenceValidationSchema`` / ``LogCreateResponse`` round-trip via
  ``model_dump(mode="json")`` + ``model_validate``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from map_types import (
    EvidenceValidationSchema,
    EvidenceWarningSchema,
    ExperimentLogCreate,
    LogCreateResponse,
)

pytestmark = pytest.mark.slow


# --- plan fixtures (frontmatter YAML evidence_keys) ------------------------

_PLAN_WITH_KEYS = (
    "---\n"
    "evidence_keys:\n"
    "  - pytest_summary\n"
    "  - alembic_current\n"
    "  - api_health\n"
    "---\n"
    "# Plan body\n"
    "## acceptance\n- (a) ...\n"
)

_PLAN_WITH_BAD_YAML = (
    "---\n"
    "evidence_keys: scalar_not_list\n"
    "---\n"
    "# Plan body\n"
)

_PLAN_NO_FRONTMATTER = "# Plain plan\n\n## acceptance\n- (a) ...\n"


def _create_experiment_with_plan(
    client: TestClient,
    auth_headers: dict[str, str],
    project_id: str,
    plan_md: str,
    reviewer_headers: dict[str, str],
) -> str:
    """Create + review + approve + start an experiment with ``plan_md``.

    Returns the experiment id (in ``running`` phase, ready to accept logs).
    """
    exp = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=auth_headers,
        json={"title": "evidence log test", "plan": {"content_md": plan_md}, "submit_for_review": True},
    ).json()
    exp_id = exp["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_headers,
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer_headers,
                json={"status": "resolved"},
            )

    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    return exp_id


# --- API endpoint: response shape ------------------------------------------


def test_create_log_returns_wrapper_shape(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """POST /logs must return ``{log, validation}`` wrapper (I1.c)."""
    exp_id = _create_experiment_with_plan(
        client, auth_headers, project["id"], _PLAN_NO_FRONTMATTER, reviewer["headers"]
    )

    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={"summary": "first log", "content_md": "body"},
    )
    assert resp.status_code == 201
    body = resp.json()

    # Wrapper shape: top-level keys are "log" + "validation"
    assert set(body.keys()) == {"log", "validation"}

    # log is the v1 ExperimentLogRead shape (so v1 consumers still work)
    log = body["log"]
    assert log["summary"] == "first log"
    assert log["content_md"] == "body"
    assert "metadata_json" in log
    assert "id" in log
    assert "experiment_id" in log
    assert "created_at" in log

    # validation defaults (no plan evidence_keys → empty)
    validation = body["validation"]
    assert validation["warnings"] == []
    assert validation["parse_error"] is None
    assert validation["plan_keys"] == []
    assert validation["valid"] is True


def test_create_log_warning_when_plan_keys_partial(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """Plan declares 3 evidence_keys; metadata covers 1 → 2 warnings."""
    exp_id = _create_experiment_with_plan(
        client, auth_headers, project["id"], _PLAN_WITH_KEYS, reviewer["headers"]
    )

    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={
            "summary": "partial evidence",
            "content_md": "body",
            "metadata": {"pytest_summary": "12 passed"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()

    validation = body["validation"]
    assert validation["valid"] is True  # soft: still True even with warnings
    assert validation["parse_error"] is None
    assert validation["plan_keys"] == ["pytest_summary", "alembic_current", "api_health"]

    warnings = validation["warnings"]
    assert len(warnings) == 2
    codes = {w["code"] for w in warnings}
    assert codes == {"MISSING_EVIDENCE_KEY"}
    missing = {w["missing_key"] for w in warnings}
    assert missing == {"alembic_current", "api_health"}
    for w in warnings:
        assert w["plan_required"] is True
        assert w["log_provided"] is False

    # log still saved despite warnings
    assert body["log"]["metadata_json"] == {"pytest_summary": "12 passed"}


def test_create_log_no_warning_when_metadata_covers_all(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """All plan keys present → empty warnings."""
    exp_id = _create_experiment_with_plan(
        client, auth_headers, project["id"], _PLAN_WITH_KEYS, reviewer["headers"]
    )

    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={
            "summary": "full evidence",
            "content_md": "body",
            "metadata": {
                "pytest_summary": "12 passed",
                "alembic_current": "035 (head)",
                "api_health": "200",
            },
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["validation"]["warnings"] == []
    assert body["validation"]["plan_keys"] == ["pytest_summary", "alembic_current", "api_health"]
    assert body["validation"]["valid"] is True


def test_create_log_parse_error_in_plan_sets_parse_error_field(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """Plan frontmatter parses to non-list value → parse_error set, log still saved."""
    exp_id = _create_experiment_with_plan(
        client, auth_headers, project["id"], _PLAN_WITH_BAD_YAML, reviewer["headers"]
    )

    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={"summary": "log on bad yaml", "content_md": "body"},
    )
    assert resp.status_code == 201
    body = resp.json()

    validation = body["validation"]
    assert validation["valid"] is True  # soft: never blocks
    assert validation["parse_error"] is not None
    assert "evidence_keys" in validation["parse_error"].lower() or "list" in validation["parse_error"].lower()
    # No warnings when parse fails (can't determine which keys are missing)
    assert validation["warnings"] == []
    assert validation["plan_keys"] == []

    # Log was still saved despite plan parse error
    assert body["log"]["summary"] == "log on bad yaml"


def test_create_log_always_201_even_when_validation_flags_issues(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """Soft validation invariant: any validation state → 201 + log persisted."""
    exp_id = _create_experiment_with_plan(
        client, auth_headers, project["id"], _PLAN_WITH_KEYS, reviewer["headers"]
    )

    # No metadata at all → all keys missing → all warnings → still 201
    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={"summary": "no metadata", "content_md": "body"},
    )
    assert resp.status_code == 201
    assert resp.json()["validation"]["valid"] is True
    assert len(resp.json()["validation"]["warnings"]) == 3


# --- SDK client: return type + shape ---------------------------------------


def test_sdk_create_log_returns_log_create_response(
    map_client, auth_headers, reviewer, project, client: TestClient
) -> None:
    """MAPClient.create_log now returns LogCreateResponse (was ExperimentLogRead)."""
    exp_id = _create_experiment_with_plan(
        client, auth_headers, project["id"], _PLAN_NO_FRONTMATTER, reviewer["headers"]
    )

    payload = ExperimentLogCreate(summary="sdk log", content_md="sdk body")
    result = map_client.create_log(exp_id, payload)  # type: ignore[attr-defined]

    assert isinstance(result, LogCreateResponse)
    assert result.log.summary == "sdk log"
    assert result.log.content_md == "sdk body"
    assert isinstance(result.validation, EvidenceValidationSchema)
    assert result.validation.valid is True
    assert result.validation.warnings == []


def test_sdk_create_log_carries_warnings_through(
    map_client, auth_headers, reviewer, project, client: TestClient
) -> None:
    """Warnings emitted by server surface as EvidenceWarningSchema on the SDK side."""
    exp_id = _create_experiment_with_plan(
        client, auth_headers, project["id"], _PLAN_WITH_KEYS, reviewer["headers"]
    )

    payload = ExperimentLogCreate(
        summary="partial sdk log",
        content_md="body",
        metadata={"pytest_summary": "ok"},
    )
    result = map_client.create_log(exp_id, payload)  # type: ignore[attr-defined]

    assert isinstance(result, LogCreateResponse)
    assert len(result.validation.warnings) == 2
    for w in result.validation.warnings:
        assert isinstance(w, EvidenceWarningSchema)
        assert w.code == "MISSING_EVIDENCE_KEY"
        assert w.plan_required is True
        assert w.log_provided is False


# --- schema round-trip -----------------------------------------------------


def test_log_create_response_model_validate_roundtrip() -> None:
    """Both wrapper and nested schemas round-trip via model_dump(mode='json')."""
    raw = {
        "log": {
            "id": "11111111-1111-1111-1111-111111111111",
            "experiment_id": "22222222-2222-2222-2222-222222222222",
            "author_agent_id": "33333333-3333-3333-3333-333333333333",
            "summary": "rt",
            "content_md": "body",
            "metadata_json": {"pytest_summary": "ok"},
            "created_at": "2026-07-08T10:00:00",
        },
        "validation": {
            "warnings": [
                {
                    "code": "MISSING_EVIDENCE_KEY",
                    "missing_key": "api_health",
                    "plan_required": True,
                    "log_provided": False,
                }
            ],
            "parse_error": None,
            "plan_keys": ["pytest_summary", "api_health"],
            "valid": True,
        },
    }
    parsed = LogCreateResponse.model_validate(raw)
    assert parsed.log.id is not None
    assert parsed.log.summary == "rt"
    assert len(parsed.validation.warnings) == 1
    assert parsed.validation.warnings[0].missing_key == "api_health"
    assert parsed.validation.plan_keys == ["pytest_summary", "api_health"]

    # dump + re-parse (mode='json' for HTTP transport)
    dumped = parsed.model_dump(mode="json")
    assert "log" in dumped and "validation" in dumped
    re_parsed = LogCreateResponse.model_validate(dumped)
    assert re_parsed == parsed


def test_evidence_validation_schema_defaults() -> None:
    """All defaults ensure minimal-arg construction works for empty results."""
    empty = EvidenceValidationSchema()
    assert empty.warnings == []
    assert empty.parse_error is None
    assert empty.plan_keys == []
    assert empty.valid is True
