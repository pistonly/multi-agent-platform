"""0db51e10 I2(5e): cross-persona acceptance_status compare audit (服务端 endpoint + SDK helper).

Pins plan 5e acceptance:

> 服务端 audit kind ``cross_persona_call`` + endpoint ``POST
> /experiments/{id}/cross-persona-call`` + SDK ``record_cross_persona_call``
> + CLI facade audit 写入

Covers:

1. ``audit_service.log_cross_persona_call_no_commit`` writes an
   ``AuditLog`` row with ``action=cross_persona_call`` and the 5-field
   payload schema.
2. ``POST /experiments/{experiment_id}/cross-persona-call`` returns 201
   with an ``AuditLogRead`` body and commits one row whose
   ``payload_json`` matches the request body.
3. SDK ``MAPClient.record_cross_persona_call`` posts the 5-field payload
   to the same path and parses the response via ``AuditLogRead``.

The CLI facade integration (``map experiment status --persona-compare``)
calls ``record_cross_persona_call`` after rendering the diff table — that
contract is pinned in ``test_cli.py`` (see
``test_cli_experiment_status_persona_compare_writes_audit`` and
``..._audit_failure_does_not_break_output``).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.domain.models import AuditLog
from server.services import audit_service
from server.services.audit_service import CROSS_PERSONA_CALL

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_experiment(client: TestClient, auth_headers: dict, project: dict, title: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": title, "plan": {"content_md": "## 计划"}},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# (1) audit_service helper
# ---------------------------------------------------------------------------


def test_audit_service_log_cross_persona_call_writes_expected_payload(
    db_session, agent_token, project, admin_headers
):
    """``log_cross_persona_call_no_commit`` persists the 5-field payload
    with ``action=cross_persona_call`` and ``target_type=experiment``.
    """
    agent_id, _ = agent_token
    experiment_id = uuid.uuid4()
    visibility_diff = {
        "actions": {"host": ["complete"], "reviewer": []},
        "phase_owner": {"host": "host", "reviewer": "reviewer"},
    }
    entry = audit_service.log_cross_persona_call_no_commit(
        db_session,
        caller_agent_id=uuid.UUID(agent_id),
        target_experiment_id=experiment_id,
        project_id=uuid.UUID(project["id"]),
        visibility_diff=visibility_diff,
        result_partition_count=2,
        diff_size=2,
    )
    db_session.commit()
    db_session.refresh(entry)
    assert entry.action == CROSS_PERSONA_CALL
    assert entry.target_type == "experiment"
    assert entry.target_id == experiment_id
    assert entry.agent_id == uuid.UUID(agent_id)
    assert entry.payload_json["caller_agent_id"] == agent_id
    assert entry.payload_json["target_experiment_id"] == str(experiment_id)
    assert entry.payload_json["visibility_diff"] == visibility_diff
    assert entry.payload_json["result_partition_count"] == 2
    assert entry.payload_json["diff_size"] == 2


# ---------------------------------------------------------------------------
# (2) Server endpoint
# ---------------------------------------------------------------------------


def test_post_cross_persona_call_endpoint_writes_audit_row(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session,
    project,
):
    """``POST /experiments/{id}/cross-persona-call`` returns 201 with an
    ``AuditLogRead`` body and commits one ``cross_persona_call`` row whose
    ``payload_json`` matches the request body."""
    experiment = _create_experiment(
        client, auth_headers, project, "cross-persona-call audit endpoint 实验"
    )
    exp_id = experiment["id"]
    payload = {
        "visibility_diff": {
            "actions": {"host": ["complete"], "reviewer": []},
            "blocked_on": {"host": None, "reviewer": "awaiting_result_approval"},
        },
        "result_partition_count": 2,
        "diff_size": 1,
    }

    response = client.post(
        f"/api/v1/experiments/{exp_id}/cross-persona-call",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["action"] == CROSS_PERSONA_CALL
    assert body["target_type"] == "experiment"
    assert body["target_id"] == exp_id

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == CROSS_PERSONA_CALL)
    ).all()
    assert len(rows) == 1
    assert rows[0].target_id == uuid.UUID(exp_id)
    assert rows[0].payload_json["visibility_diff"] == payload["visibility_diff"]
    assert rows[0].payload_json["result_partition_count"] == 2
    assert rows[0].payload_json["diff_size"] == 1


def test_post_cross_persona_call_endpoint_404_for_unknown_experiment(
    client: TestClient,
    auth_headers: dict[str, str],
):
    response = client.post(
        f"/api/v1/experiments/{uuid.uuid4()}/cross-persona-call",
        headers=auth_headers,
        json={"visibility_diff": {}, "result_partition_count": 0, "diff_size": 0},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# (3) SDK helper
# ---------------------------------------------------------------------------


def test_sdk_record_cross_persona_call_posts_expected_payload(monkeypatch):
    """``MAPClient.record_cross_persona_call`` posts the 5-field payload
    to ``/experiments/{id}/cross-persona-call`` and parses the response
    via ``AuditLogRead``."""
    from map_client.client import MAPClient
    from map_types.schemas import AuditLogRead, CrossPersonaCallRecord

    exp_id = uuid.uuid4()
    captured: dict = {}

    def fake_request(self, method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = kwargs.get("params")
        captured["json"] = kwargs.get("json")
        # Return a minimal AuditLogRead-shaped payload; the SDK must
        # validate via Pydantic and not reject.
        return {
            "id": str(uuid.uuid4()),
            "action": CROSS_PERSONA_CALL,
            "target_type": "experiment",
            "target_id": str(exp_id),
            "agent_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "summary": None,
            "payload_json": kwargs.get("json"),
            "created_at": "2026-07-08T00:00:00Z",
        }

    monkeypatch.setattr(MAPClient, "_json", fake_request)

    client = MAPClient("http://test", token="t")
    result = client.record_cross_persona_call(
        exp_id,
        visibility_diff={"actions": {"host": ["complete"], "reviewer": []}},
        result_partition_count=2,
        diff_size=1,
    )
    assert isinstance(result, AuditLogRead)
    assert captured["method"] == "POST"
    assert captured["path"] == f"/experiments/{exp_id}/cross-persona-call"
    body = captured["json"]
    # Body must validate against the same Pydantic schema the server uses.
    CrossPersonaCallRecord.model_validate(body)
    assert body["result_partition_count"] == 2
    assert body["diff_size"] == 1
    assert body["visibility_diff"]["actions"]["host"] == ["complete"]
