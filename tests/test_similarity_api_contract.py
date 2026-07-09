"""b72d0542 I1.b(2)(b)(e)(g) — API contract tests for log content similarity
soft warning + ``--force-skip-similarity`` + ``log.force_skip`` audit.

Verifies the contract from plan (b)+(e)+(g):

| Case | body pattern                              | similarity_warning | force_skip | audit row |
|------|-------------------------------------------|--------------------|------------|-----------|
| 1    | first log                                 | null               | False      | no        |
| 2    | second log identical to first             | HIGH_CONTENT_SIMILARITY | False | no |
| 3    | second log identical + force_skip_similarity | null           | True       | yes       |
| 4    | second log different                      | null               | False      | no        |
| 5    | force_skip_similarity on non-duplicate    | null               | False      | no (no warning fired) |

Soft validation invariant: log is always saved; warnings are advisory.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tests._frontmatter import make_valid_plan


@pytest.fixture
def running_experiment_id(
    client: TestClient,
    auth_headers: dict[str, str],
    reviewer: dict[str, str],
    project: dict,
) -> str:
    """Create + review + approve + start an experiment; return its id."""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "I1.b(2) API contract test",
            "plan": {"content_md": make_valid_plan(body="## test plan\n")},
            "submit_for_review": True,
        },
    ).json()

    review = client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer["headers"],
                json={"status": "resolved"},
            )
    client.post(f"/api/v1/experiments/{exp['id']}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp['id']}/start", headers=auth_headers)
    return exp["id"]


def _append_log(
    client: TestClient,
    auth_headers: dict[str, str],
    exp_id: str,
    body: str,
    *,
    force_skip_similarity: bool = False,
):
    return client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={
            "summary": f"log {uuid.uuid4().hex[:8]}",
            "content_md": body,
            "metadata": {"pytest_summary": "ok"},
            "force_skip_similarity": force_skip_similarity,
        },
    )


# --- case 1: first log on the experiment ----------------------------------


def test_case_1_first_log_emits_no_similarity_warning(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    response = _append_log(client, auth_headers, running_experiment_id, "first log body")
    assert response.status_code == 201, response.text

    payload = response.json()
    assert payload["similarity_warning"] is None
    assert payload["force_skip"] is False
    assert payload["log"]["content_md"] == "first log body"


# --- case 2: duplicate body without force ----------------------------------


def test_case_2_duplicate_body_emits_warning_without_force(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    body = "duplicate body"
    first = _append_log(client, auth_headers, running_experiment_id, body)
    assert first.status_code == 201

    second = _append_log(client, auth_headers, running_experiment_id, body)
    assert second.status_code == 201

    payload = second.json()
    sim = payload["similarity_warning"]
    assert sim is not None
    assert sim["code"] == "HIGH_CONTENT_SIMILARITY"
    assert sim["score"] == 1.0
    assert sim["threshold"] == 0.7
    assert sim["model"] == "placeholder:jaccard-v0"
    assert sim["ref_log_id"] == first.json()["log"]["id"]
    assert payload["force_skip"] is False


# --- case 3: duplicate body with force-skip-similarity ----------------------


def test_case_3_duplicate_with_force_emits_audit_and_no_warning(
    client: TestClient,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
    running_experiment_id: str,
) -> None:
    body = "duplicate body (force)"
    first = _append_log(client, auth_headers, running_experiment_id, body)
    assert first.status_code == 201

    second = _append_log(
        client, auth_headers, running_experiment_id, body, force_skip_similarity=True
    )
    assert second.status_code == 201

    payload = second.json()
    assert payload["similarity_warning"] is None
    assert payload["force_skip"] is True

    # The audit row carries log.force_skip with the suppressed warning
    # details (plan §e: 5-field payload). Admin endpoint only.
    audit = client.get(
        "/api/v1/admin/audit", params={"kind": "log.force_skip"}, headers=admin_headers
    )
    assert audit.status_code == 200, audit.text
    items = audit.json()
    matches = [
        item
        for item in items
        if item.get("payload_json", {}).get("log_id") == payload["log"]["id"]
    ]
    assert len(matches) == 1, items
    audit_row = matches[0]
    payload_json = audit_row["payload_json"]
    assert payload_json["similarity_score"] == 1.0
    assert payload_json["threshold"] == 0.7
    assert payload_json["embedding_model"] == "placeholder:jaccard-v0"
    assert payload_json["ref_log_id"] == first.json()["log"]["id"]
    assert audit_row["action"] == "log.force_skip"


# --- case 4: different body ------------------------------------------------


def test_case_4_different_body_no_warning(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    first = _append_log(client, auth_headers, running_experiment_id, "body A")
    assert first.status_code == 201

    second = _append_log(
        client, auth_headers, running_experiment_id, "completely different body B"
    )
    assert second.status_code == 201

    payload = second.json()
    assert payload["similarity_warning"] is None
    assert payload["force_skip"] is False


# --- case 5: force flag on non-duplicate is a no-op -----------------------


def test_case_5_force_on_non_duplicate_no_audit_no_warning(
    client: TestClient,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
    running_experiment_id: str,
) -> None:
    """``--force-skip-similarity`` is a no-op when no warning fired.

    The audit row is only written when a warning was actually suppressed.
    """
    first = _append_log(client, auth_headers, running_experiment_id, "body A")
    assert first.status_code == 201

    second = _append_log(
        client,
        auth_headers,
        running_experiment_id,
        "completely different body B",
        force_skip_similarity=True,
    )
    assert second.status_code == 201

    payload = second.json()
    assert payload["similarity_warning"] is None
    assert payload["force_skip"] is False

    audit = client.get(
        "/api/v1/admin/audit", params={"kind": "log.force_skip"}, headers=admin_headers
    )
    items = audit.json()
    log_ids = [item.get("payload_json", {}).get("log_id") for item in items]
    assert payload["log"]["id"] not in log_ids
