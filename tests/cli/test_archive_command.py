"""CLI tests for `topic archive` / `experiment archive` (v0.7 P3)."""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from map_client import project_config
from map_client.testing import MAPTestClientTransport
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli(monkeypatch, client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    # Honor ``MAP_TOKEN`` at call time so tests can swap personas via
    # ``monkeypatch.setenv("MAP_TOKEN", ...)`` (matches real ``load_config``
    # which reads the env var). Without this, a later fixture's patch would
    # pin the token and silently defeat persona switching.
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {
            "api_url": "http://test",
            "token": os.environ.get("MAP_TOKEN", token),
            "project_key": None,
        },
    )


@pytest.fixture
def patched_reviewer_cli(monkeypatch, client, reviewer):
    token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {
            "api_url": "http://test",
            "token": os.environ.get("MAP_TOKEN", token),
            "project_key": None,
        },
    )


# helpers ---------------------------------------------------------------------


def _create_topic(client, headers, project, title: str = "归档测试话题") -> str:
    resp = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=headers,
        json={"title": title},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_experiment(client, headers, project, title: str = "归档测试实验") -> str:
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=headers,
        json={"title": title, "plan": {"content_md": "p"}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# happy paths (real server round-trip via MAPTestClientTransport)
# ---------------------------------------------------------------------------


def test_topic_archive_basic_success(runner: CliRunner, patched_cli, client, project, auth_headers):
    topic_id = _create_topic(client, auth_headers, project, title="basic-archive")

    result = runner.invoke(app, ["topic", "archive", "--id", topic_id])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["id"] == topic_id
    assert payload["archived_at"] is not None

    # default list excludes archived → empty
    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json() == []

    # include_archived shows the topic
    with_archived = client.get(
        f"/api/v1/projects/{project['id']}/topics?include_archived=true",
        headers=auth_headers,
    )
    assert len(with_archived.json()) == 1


def test_topic_archive_undo_success(runner: CliRunner, patched_cli, client, project, auth_headers):
    topic_id = _create_topic(client, auth_headers, project, title="undo-archive")

    # archive first
    first = runner.invoke(app, ["topic", "archive", "--id", topic_id])
    assert first.exit_code == 0, first.output
    assert yaml.safe_load(first.output)["archived_at"] is not None

    # undo
    undo = runner.invoke(app, ["topic", "archive", "--id", topic_id, "--undo"])
    assert undo.exit_code == 0, undo.output
    assert yaml.safe_load(undo.output)["archived_at"] is None

    # back in default list
    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert len(listing.json()) == 1


def test_experiment_archive_basic_success(
    runner: CliRunner, patched_cli, client, project, auth_headers
):
    exp_id = _create_experiment(client, auth_headers, project, title="exp-archive")

    result = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["id"] == exp_id
    assert payload["archived_at"] is not None

    listing = client.get(
        f"/api/v1/projects/{project['id']}/experiments", headers=auth_headers
    )
    assert listing.json() == []

    with_archived = client.get(
        f"/api/v1/projects/{project['id']}/experiments?include_archived=true",
        headers=auth_headers,
    )
    assert len(with_archived.json()) == 1


def test_experiment_archive_undo_success(
    runner: CliRunner, patched_cli, client, project, auth_headers
):
    exp_id = _create_experiment(client, auth_headers, project, title="exp-undo")

    first = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert first.exit_code == 0, first.output

    undo = runner.invoke(app, ["experiment", "archive", "--id", exp_id, "--undo"])
    assert undo.exit_code == 0, undo.output
    assert yaml.safe_load(undo.output)["archived_at"] is None


# ---------------------------------------------------------------------------
# --undo / --unarchive equivalence (mock SDK to capture payload)
# ---------------------------------------------------------------------------


def test_undo_and_unarchive_equivalent(runner: CliRunner, patched_cli):
    topic_id = str(uuid.uuid4())
    captured: list[Any] = []

    class _FakeSummary:
        def __init__(self, ts: str, tid: str) -> None:
            self.archived_at = ts
            self.id = tid
            self.warnings: list[str] = []

        def model_dump(self, mode: str | None = None) -> dict[str, Any]:
            return {"id": self.id, "archived_at": self.archived_at}

    def fake_update_topic(self, target_id, payload):
        captured.append(payload.model_dump(exclude_unset=True))
        return _FakeSummary("2026-06-30T00:00:00Z", str(target_id))

    with patch.object(cli_main.MAPClient, "update_topic", fake_update_topic):
        r_undo = runner.invoke(app, ["topic", "archive", "--id", topic_id, "--undo"])
        r_unarchive = runner.invoke(app, ["topic", "archive", "--id", topic_id, "--unarchive"])

    # Both invocations must succeed and forward identical payload (archived=False)
    assert r_undo.exit_code == 0, r_undo.output
    assert r_unarchive.exit_code == 0, r_unarchive.output
    assert len(captured) == 2
    assert captured[0] == {"archived": False}
    assert captured[1] == {"archived": False}


# ---------------------------------------------------------------------------
# idempotent timestamp (mock SDK to control response shape)
# ---------------------------------------------------------------------------


def test_archive_idempotent_returns_same_timestamp(runner: CliRunner, patched_cli):
    """Mock SDK so two consecutive archives return the SAME archived_at,
    verifying CLI forwards `archived=true` both times (idempotency at the
    call-site level — server-side response shape is fixed)."""
    fixed_ts = "2026-06-30T00:00:00+00:00"
    captured: list[Any] = []

    class _FakeSummary:
        def __init__(self, ts: str, tid: str) -> None:
            self.archived_at = ts
            self.id = tid
            self.warnings: list[str] = []

        def model_dump(self, mode: str | None = None) -> dict[str, Any]:
            return {"id": self.id, "archived_at": self.archived_at}

    def fake_update_topic(self, target_id, payload):
        captured.append(payload.model_dump(exclude_unset=True))
        return _FakeSummary(fixed_ts, str(target_id))

    with patch.object(cli_main.MAPClient, "update_topic", fake_update_topic):
        topic_id = str(uuid.uuid4())
        r1 = runner.invoke(app, ["topic", "archive", "--id", topic_id])
        r2 = runner.invoke(app, ["topic", "archive", "--id", topic_id])

    assert r1.exit_code == 0, r1.output
    assert r2.exit_code == 0, r2.output
    body1 = yaml.safe_load(r1.output)
    body2 = yaml.safe_load(r2.output)
    assert body1["archived_at"] == fixed_ts
    assert body2["archived_at"] == fixed_ts
    assert captured == [{"archived": True}, {"archived": True}]


# ---------------------------------------------------------------------------
# error paths (404 friendly message; typer validation)
# ---------------------------------------------------------------------------


def test_archive_not_found_404_friendly_message(
    runner: CliRunner, patched_cli, client, auth_headers
):
    missing_topic = str(uuid.uuid4())
    result = runner.invoke(app, ["topic", "archive", "--id", missing_topic])
    assert result.exit_code == 1, result.output
    assert f"Error: topic {missing_topic} not found" in result.output

    missing_exp = str(uuid.uuid4())
    result2 = runner.invoke(app, ["experiment", "archive", "--id", missing_exp])
    assert result2.exit_code == 1, result2.output
    assert f"Error: experiment {missing_exp} not found" in result2.output


def test_archive_invalid_uuid_typer_error(runner: CliRunner, patched_cli):
    bad = "not-a-uuid"
    result = runner.invoke(app, ["topic", "archive", "--id", bad])
    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert bad in result.output

    result2 = runner.invoke(app, ["experiment", "archive", "--id", bad])
    assert result2.exit_code != 0
    assert "Invalid value" in result2.output


def test_archive_missing_id_typer_error(runner: CliRunner, patched_cli):
    result = runner.invoke(app, ["topic", "archive"])
    assert result.exit_code != 0
    assert "Missing option" in result.output
    assert "--id" in result.output

    result2 = runner.invoke(app, ["experiment", "archive"])
    assert result2.exit_code != 0
    assert "Missing option" in result2.output
    assert "--id" in result2.output


# ---------------------------------------------------------------------------
# permission matrix (reviewer persona can archive too)
# ---------------------------------------------------------------------------


def test_archive_permission_matrix(
    runner: CliRunner,
    monkeypatch,
    patched_cli,
    patched_reviewer_cli,
    client,
    project,
    auth_headers,
    reviewer,
):
    """Cover ≥2 personas: host archives; reviewer unarchives.

    Fixture ordering means ``patched_reviewer_cli`` runs after ``patched_cli``
    (which sets ``MAP_TOKEN`` to the host token). To exercise BOTH personas
    we explicitly swap ``MAP_TOKEN`` between invocations via monkeypatch.
    """
    host_token = auth_headers["Authorization"].removeprefix("Bearer ")
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")

    # Setup: ensure we start on the host persona and create fixtures via HTTP
    monkeypatch.setenv("MAP_TOKEN", host_token)
    topic_id = _create_topic(client, auth_headers, project, title="perm-topic")
    exp_id = _create_experiment(client, auth_headers, project, title="perm-exp")

    # host archives successfully
    r1 = runner.invoke(app, ["topic", "archive", "--id", topic_id])
    assert r1.exit_code == 0, r1.output

    r2 = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert r2.exit_code == 0, r2.output

    # swap to reviewer persona and unarchive
    monkeypatch.setenv("MAP_TOKEN", reviewer_token)
    r3 = runner.invoke(app, ["topic", "archive", "--id", topic_id, "--undo"])
    assert r3.exit_code == 0, r3.output

    r4 = runner.invoke(app, ["experiment", "archive", "--id", exp_id, "--undo"])
    assert r4.exit_code == 0, r4.output


# ---------------------------------------------------------------------------
# show visibility after archive
# ---------------------------------------------------------------------------


def test_show_after_archive_still_visible_with_archived_at(
    runner: CliRunner, patched_cli, client, project, auth_headers
):
    topic_id = _create_topic(client, auth_headers, project, title="show-after-archive")

    archive = runner.invoke(app, ["topic", "archive", "--id", topic_id])
    assert archive.exit_code == 0, archive.output
    archived_payload = yaml.safe_load(archive.output)
    assert archived_payload["archived_at"] is not None

    # show command still works and returns the topic with archived_at
    show = runner.invoke(app, ["topic", "show", "--id", topic_id])
    assert show.exit_code == 0, show.output
    show_payload = yaml.safe_load(show.output)
    assert show_payload["id"] == topic_id
    assert show_payload["archived_at"] is not None

    # experiment variant
    exp_id = _create_experiment(client, auth_headers, project, title="exp-show-after-archive")
    archive_exp = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert archive_exp.exit_code == 0, archive_exp.output
    exp_payload = yaml.safe_load(archive_exp.output)
    assert exp_payload["archived_at"] is not None

    show_exp = runner.invoke(app, ["experiment", "show", "--id", exp_id])
    assert show_exp.exit_code == 0, show_exp.output
    exp_show = yaml.safe_load(show_exp.output)
    assert exp_show["id"] == exp_id
    assert exp_show["archived_at"] is not None


# ---------------------------------------------------------------------------
# help text — confirm both --undo and --unarchive are advertised
# ---------------------------------------------------------------------------


def test_archive_help_advertises_undo_and_unarchive(runner: CliRunner, patched_cli):
    topic_help = runner.invoke(app, ["topic", "archive", "--help"])
    assert topic_help.exit_code == 0, topic_help.output
    assert "--undo" in topic_help.output
    assert "--unarchive" in topic_help.output

    exp_help = runner.invoke(app, ["experiment", "archive", "--help"])
    assert exp_help.exit_code == 0, exp_help.output
    assert "--undo" in exp_help.output
    assert "--unarchive" in exp_help.output


# ---------------------------------------------------------------------------
# audit best-effort (criterion 13):
# If the server records audit events for archive/undo, assert their presence.
# If not implemented (current state), the test is xfail so it documents the
# gap without blocking CI — backlog item per plan "后续" section.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "Server-side audit on archive/undo not implemented yet "
        "(see plan '后续' backlog). Marked xfail per criterion 13 best-effort."
    ),
    strict=False,
)
def test_archive_emits_audit_event(
    runner: CliRunner, patched_cli, client, project, auth_headers
):
    topic_id = _create_topic(client, auth_headers, project, title="audit-topic")
    exp_id = _create_experiment(client, auth_headers, project, title="audit-exp")

    # archive both
    r_t = runner.invoke(app, ["topic", "archive", "--id", topic_id])
    assert r_t.exit_code == 0, r_t.output
    r_e = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert r_e.exit_code == 0, r_e.output

    # undo both
    r_t2 = runner.invoke(app, ["topic", "archive", "--id", topic_id, "--undo"])
    assert r_t2.exit_code == 0, r_t2.output
    r_e2 = runner.invoke(app, ["experiment", "archive", "--id", exp_id, "--undo"])
    assert r_e2.exit_code == 0, r_e2.output

    # query audit log for each target
    topic_audit = client.get(
        "/api/v1/audit",
        params={"target_type": "topic", "target_id": topic_id},
        headers=auth_headers,
    )
    exp_audit = client.get(
        "/api/v1/audit",
        params={"target_type": "experiment", "target_id": exp_id},
        headers=auth_headers,
    )
    # when xfail: any failure is acceptable. When passing: at least one entry
    # whose action contains 'archived' or 'unarchived' must be present.
    if topic_audit.status_code == 200 and exp_audit.status_code == 200:
        topic_actions = [entry.get("action", "") for entry in topic_audit.json()]
        exp_actions = [entry.get("action", "") for entry in exp_audit.json()]
        assert any("archived" in a or "unarchived" in a for a in topic_actions), (
            f"expected an archive audit event for topic, got {topic_actions}"
        )
        assert any("archived" in a or "unarchived" in a for a in exp_actions), (
            f"expected an archive audit event for experiment, got {exp_actions}"
        )
    else:
        pytest.xfail(
            f"audit endpoint returned "
            f"topic={topic_audit.status_code}, exp={exp_audit.status_code}"
        )
