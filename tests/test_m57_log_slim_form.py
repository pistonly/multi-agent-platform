"""PRD v0.13 M57 — experiment log slim form (``file_path``) contract tests.

Server side (TestClient, full-lifecycle experiment in ``running`` phase):
* slim form persists a stub ``content_md`` (``See file: <path>``) plus the
  ``file_path`` on the row; ``log_index`` keeps incrementing; the list read
  path exposes ``file_path``.
* slim form skips the content-similarity check EXPLICITLY
  (``similarity_skipped == "slim form"``, no warning, no audit row) and
  ``force_skip_similarity`` is a no-op by construction.
* anti-abuse hint: an exact summary repeat of the prior log fires
  ``summary_repeat_hint`` (hint, never a warning; log still saved).
* evidence validation is metadata-driven → identical between slim and
  full forms given the same metadata (r1 review 73a24940 premise).
* both forms missing → 422 (dual-form validator).
* full ``content_md`` form regression: similarity check still fires and
  ``similarity_skipped`` stays None.

CLI side (CliRunner + stub transport):
* ``--file`` + ``--log-file-path`` together → exit 2 mutual-exclusion error.
* ``--file`` regression: request body carries ``content_md``.
* ``--log-file-path``: request body carries ``file_path`` and does NOT
  send ``force_skip_similarity`` (no-op by protocol, plan M57D).
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cli import main as cli_main
from cli.main import app
from server.domain.models import AuditLog
from tests._frontmatter import make_valid_plan

# --- plan fixture -----------------------------------------------------------

_PLAN = make_valid_plan(
    body="# Plan body\n## acceptance\n- (a) ...\n",
    evidence_keys=["pytest_summary", "alembic_current"],
)


def _running_experiment(
    client: TestClient,
    auth_headers: dict[str, str],
    project: dict,
    reviewer_headers: dict[str, str],
) -> str:
    """Create + review + approve + start → experiment id in ``running``."""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "m57 slim log test",
            "plan": {"content_md": _PLAN},
            "submit_for_review": True,
        },
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


def _post_log(client: TestClient, headers: dict[str, str], exp_id: str, **body):
    return client.post(f"/api/v1/experiments/{exp_id}/logs", headers=headers, json=body)


# --- server: slim form persistence + read path ------------------------------


def test_slim_form_persists_stub_path_and_increments_index(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    exp_id = _running_experiment(client, auth_headers, project, reviewer["headers"])

    r1 = _post_log(
        client,
        auth_headers,
        exp_id,
        summary="slim log one",
        file_path="map/experiments/x/log-r1.md",
    )
    assert r1.status_code == 201, r1.text
    body = r1.json()
    assert body["log"]["content_md"] == "See file: map/experiments/x/log-r1.md"
    assert body["log"]["file_path"] == "map/experiments/x/log-r1.md"

    r2 = _post_log(
        client,
        auth_headers,
        exp_id,
        summary="slim log two",
        file_path="map/experiments/x/log-r2.md",
    )
    assert r2.status_code == 201

    # List read path (ordered by log_index asc) exposes file_path + stub.
    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers).json()
    assert len(logs) == 2
    assert logs[0]["file_path"] == "map/experiments/x/log-r1.md"
    assert logs[1]["file_path"] == "map/experiments/x/log-r2.md"
    assert logs[0]["content_md"].startswith("See file:")
    assert logs[1]["content_md"].startswith("See file:")


def test_slim_form_marks_similarity_skipped(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    exp_id = _running_experiment(client, auth_headers, project, reviewer["headers"])
    resp = _post_log(client, auth_headers, exp_id, summary="slim", file_path="a/b.md")
    assert resp.status_code == 201
    body = resp.json()
    # Explicit skip marker, never a silent empty warning list (M57D).
    assert body["similarity_skipped"] == "slim form"
    assert body["similarity_warning"] is None
    assert body["force_skip"] is False


def test_slim_form_force_skip_is_noop_without_audit(
    client: TestClient,
    auth_headers: dict[str, str],
    reviewer: dict,
    project: dict,
    db_session,
) -> None:
    """Slim form + force_skip_similarity=True → no audit row, no echo."""
    exp_id = _running_experiment(client, auth_headers, project, reviewer["headers"])
    resp = _post_log(
        client,
        auth_headers,
        exp_id,
        summary="slim forced",
        file_path="a/b.md",
        force_skip_similarity=True,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["force_skip"] is False
    assert body["similarity_warning"] is None

    force_skip_rows = db_session.query(AuditLog).filter(AuditLog.action == "log.force_skip").count()
    assert force_skip_rows == 0


def test_slim_form_summary_repeat_hint(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """Exact summary repeat → non-blocking hint; different summary → None."""
    exp_id = _running_experiment(client, auth_headers, project, reviewer["headers"])

    first = _post_log(client, auth_headers, exp_id, summary="same summary", file_path="a/1.md")
    assert first.status_code == 201
    assert first.json()["summary_repeat_hint"] is None  # no prior log yet

    repeat = _post_log(client, auth_headers, exp_id, summary="same summary", file_path="a/2.md")
    assert repeat.status_code == 201  # hint never blocks
    hint = repeat.json()["summary_repeat_hint"]
    assert hint is not None
    assert "same summary" in hint  # carries the summary for self-judgement

    fresh = _post_log(client, auth_headers, exp_id, summary="different summary", file_path="a/3.md")
    assert fresh.status_code == 201
    assert fresh.json()["summary_repeat_hint"] is None


def test_evidence_validation_identical_across_forms(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """Evidence check is metadata-driven: same metadata → same validation
    regardless of slim vs full form (r1 review 73a24940)."""
    meta = {"pytest_summary": "10 passed"}  # alembic_current missing on purpose
    exp_full = _running_experiment(client, auth_headers, project, reviewer["headers"])
    full = _post_log(
        client,
        auth_headers,
        exp_full,
        summary="full",
        content_md="body",
        metadata=meta,
    ).json()

    exp_slim = _running_experiment(client, auth_headers, project, reviewer["headers"])
    slim = _post_log(
        client,
        auth_headers,
        exp_slim,
        summary="slim",
        file_path="a/b.md",
        metadata=meta,
    ).json()

    assert full["validation"] == slim["validation"]
    assert slim["validation"]["valid"] is True
    missing = {w["missing_key"] for w in slim["validation"]["warnings"]}
    assert missing == {"alembic_current"}


def test_missing_both_forms_rejected_422(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    exp_id = _running_experiment(client, auth_headers, project, reviewer["headers"])
    resp = _post_log(client, auth_headers, exp_id, summary="nothing")
    assert resp.status_code == 422


def test_full_form_regression_similarity_still_fires(
    client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict
) -> None:
    """Full content_md form unchanged: identical body twice → warning fires
    (placeholder scorer), similarity_skipped stays None, file_path None."""
    exp_id = _running_experiment(client, auth_headers, project, reviewer["headers"])
    first = _post_log(client, auth_headers, exp_id, summary="full one", content_md="same body")
    assert first.status_code == 201
    assert first.json()["similarity_skipped"] is None
    assert first.json()["log"]["file_path"] is None

    second = _post_log(client, auth_headers, exp_id, summary="full two", content_md="same body")
    assert second.status_code == 201
    body = second.json()
    assert body["similarity_skipped"] is None
    assert body["similarity_warning"] is not None
    assert body["similarity_warning"]["code"] == "HIGH_CONTENT_SIMILARITY"
    assert body["summary_repeat_hint"] is None  # full form never fires the hint


# ---- CLI level (CliRunner + stub transport) --------------------------------


_EXP_ID = "12345678-1234-5678-1234-567812345678"
_PROJECT_ID = "87654321-4321-8765-4321-210987654321"
_AGENT_ID = "abcdef01-2345-6789-abcd-ef0123456789"
_TS = "2026-08-16T10:00:00"


class LogStubTransport:
    """Records POST /logs bodies; returns a slim-form 201; else 404."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method == "POST" and path.endswith(f"/{_EXP_ID}/logs"):
            body = json.loads(request.content) if request.content else {}
            self.bodies.append(body)
            return httpx.Response(
                201,
                json={
                    "log": {
                        "id": str(uuid.uuid4()),
                        "experiment_id": _EXP_ID,
                        "author_agent_id": _AGENT_ID,
                        "summary": body.get("summary", ""),
                        "content_md": body.get("content_md") or "",
                        "file_path": body.get("file_path"),
                        "metadata_json": body.get("metadata"),
                        "created_at": _TS,
                    },
                    "validation": {
                        "warnings": [],
                        "parse_error": None,
                        "plan_keys": [],
                        "valid": True,
                    },
                    "similarity_warning": None,
                    "force_skip": False,
                    "similarity_skipped": "slim form",
                    "summary_repeat_hint": None,
                },
            )
        return httpx.Response(404, json={"detail": "unstubbed"})


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_env(monkeypatch):
    def _install(transport: httpx.BaseTransport) -> None:
        monkeypatch.setattr(cli_main, "_transport", transport)
        monkeypatch.setenv("MAP_TOKEN", "fake")
        monkeypatch.setenv("MAP_API_URL", "http://test")
        monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)

    return _install


def test_cli_mutually_exclusive_file_and_log_file_path(stub_env, runner, tmp_path) -> None:
    f = tmp_path / "log.md"
    f.write_text("# body", encoding="utf-8")
    transport = LogStubTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        [
            "experiment",
            "log",
            "--id",
            _EXP_ID,
            "--summary",
            "s",
            "--file",
            str(f),
            "--log-file-path",
            "a/b.md",
        ],
    )
    assert result.exit_code == 2
    assert "use only one of --file or --log-file-path" in result.output
    assert transport.bodies == []  # rejected before any request


def test_cli_file_form_regression_sends_content_md(stub_env, runner, tmp_path) -> None:
    f = tmp_path / "log.md"
    f.write_text("# full body", encoding="utf-8")
    transport = LogStubTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        ["experiment", "log", "--id", _EXP_ID, "--summary", "s", "--file", str(f)],
    )
    assert result.exit_code == 0, result.output
    assert len(transport.bodies) == 1
    body = transport.bodies[0]
    assert body["content_md"] == "# full body"
    assert "file_path" not in body or body["file_path"] is None


def test_cli_slim_form_sends_path_without_force_skip(stub_env, runner) -> None:
    transport = LogStubTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        [
            "experiment",
            "log",
            "--id",
            _EXP_ID,
            "--summary",
            "s",
            "--log-file-path",
            "map/experiments/x/log-r1.md",
            "--force-skip-similarity",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(transport.bodies) == 1
    body = transport.bodies[0]
    assert body["file_path"] == "map/experiments/x/log-r1.md"
    assert not body.get("content_md")
    # No-op by protocol: slim form never sends the force-skip flag (M57D).
    assert not body.get("force_skip_similarity")
