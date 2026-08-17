"""CLI tests for `topic archive` / `experiment archive` (v0.7 P3).

v0.13 M58: ``topic archive`` DB write path retired — topic cases below assert
the guidance rejection (exit 2 + fs file-move hint); experiment archive keeps
its real round-trip coverage (experiment domain is out of M58 scope).
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from map_client import project_config
from map_client.testing import MAPTestClientTransport
from sqlalchemy import select
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from server.domain.models import Agent
from tests._db_topic_factory import db_create_topic
from tests._frontmatter import make_valid_plan

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


def _db_topic(db_session, project, title: str = "归档测试话题"):
    host = db_session.scalar(select(Agent).where(Agent.name == "test-agent"))
    return db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=host.id,
        title=title,
    )


def _create_experiment(client, headers, project, title: str = "归档测试实验") -> str:
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=headers,
        json={"title": title, "plan": {"content_md": make_valid_plan(body="p")}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_cancelled_experiment(client, headers, project, title: str = "归档测试实验") -> str:
    exp_id = _create_experiment(client, headers, project, title=title)
    cancelled = client.post(f"/api/v1/experiments/{exp_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    return exp_id


def _assert_topic_archive_guidance(result: Any, kind: str = "archive") -> None:
    assert result.exit_code == 2, result.output
    assert "DB write path retired" in result.output, result.output
    if kind == "archive":
        assert "mv map/topics/<slug>/ map/archive/topics/" in result.output
    else:
        assert "mv map/archive/topics/<slug>/ map/topics/" in result.output


# ---------------------------------------------------------------------------
# topic archive: guidance rejection (v0.13 M58 — DB write path retired)
# ---------------------------------------------------------------------------


def test_topic_archive_basic_rejected_with_fs_guidance(
    runner: CliRunner, patched_cli, db_session, project
):
    topic = _db_topic(db_session, project, title="basic-archive")

    result = runner.invoke(app, ["topic", "archive", "--id", str(topic.id)])
    _assert_topic_archive_guidance(result)


def test_topic_archive_undo_and_unarchive_both_rejected(
    runner: CliRunner, patched_cli, db_session, project
):
    topic = _db_topic(db_session, project, title="undo-archive")

    undo = runner.invoke(app, ["topic", "archive", "--id", str(topic.id), "--undo"])
    _assert_topic_archive_guidance(undo, kind="archive-undo")
    unarchive = runner.invoke(app, ["topic", "archive", "--id", str(topic.id), "--unarchive"])
    _assert_topic_archive_guidance(unarchive, kind="archive-undo")
    # 两个别名路由到同一拒绝路径，文案一致
    assert undo.output == unarchive.output


def test_undo_and_unarchive_equivalent(runner: CliRunner, patched_cli):
    """topic 侧：双别名等价 = 同一引导拒绝；experiment 侧：payload 等价转发。"""
    topic_id = str(uuid.uuid4())

    with patch.object(cli_main.MAPClient, "update_topic") as fake_update_topic:
        r_undo = runner.invoke(app, ["topic", "archive", "--id", topic_id, "--undo"])
        r_unarchive = runner.invoke(app, ["topic", "archive", "--id", topic_id, "--unarchive"])

    assert r_undo.exit_code == 2, r_undo.output
    assert r_unarchive.exit_code == 2, r_unarchive.output
    assert r_undo.output == r_unarchive.output
    fake_update_topic.assert_not_called()

    # experiment 侧等价性保留（实验域不在 M58 退役面）
    captured: list[Any] = []

    class _FakeSummary:
        def __init__(self, ts: str, tid: str) -> None:
            self.archived_at = ts
            self.id = tid
            self.warnings: list[str] = []

        def model_dump(self, mode: str | None = None) -> dict[str, Any]:
            return {"id": self.id, "archived_at": self.archived_at}

    def fake_update_experiment(self, target_id, payload):
        captured.append(payload.model_dump(exclude_unset=True))
        return _FakeSummary(None, str(target_id))

    with patch.object(cli_main.MAPClient, "update_experiment", fake_update_experiment):
        e_undo = runner.invoke(app, ["experiment", "archive", "--id", topic_id, "--undo"])
        e_unarchive = runner.invoke(
            app, ["experiment", "archive", "--id", topic_id, "--unarchive"]
        )

    assert e_undo.exit_code == 0, e_undo.output
    assert e_unarchive.exit_code == 0, e_unarchive.output
    assert len(captured) == 2
    assert captured[0] == {"archived": False}
    assert captured[1] == {"archived": False}


def test_archive_rejected_before_sdk_call(runner: CliRunner, patched_cli):
    """两次调用均引导拒绝且不触达 SDK（幂等性断言随 DB 路径退役改语义）。"""
    captured: list[Any] = []

    def fake_update_topic(self, target_id, payload):
        captured.append(payload)
        return None

    with patch.object(cli_main.MAPClient, "update_topic", fake_update_topic):
        topic_id = str(uuid.uuid4())
        r1 = runner.invoke(app, ["topic", "archive", "--id", topic_id])
        r2 = runner.invoke(app, ["topic", "archive", "--id", topic_id])

    assert r1.exit_code == 2, r1.output
    assert r2.exit_code == 2, r2.output
    assert r1.output == r2.output
    assert captured == []


# ---------------------------------------------------------------------------
# experiment archive: real round-trip (experiment domain — unchanged by M58)
# ---------------------------------------------------------------------------


def test_experiment_archive_basic_success(
    runner: CliRunner, patched_cli, client, project, auth_headers
):
    exp_id = _create_cancelled_experiment(client, auth_headers, project, title="exp-archive")

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
    exp_id = _create_cancelled_experiment(client, auth_headers, project, title="exp-undo")

    first = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert first.exit_code == 0, first.output

    undo = runner.invoke(app, ["experiment", "archive", "--id", exp_id, "--undo"])
    assert undo.exit_code == 0, undo.output
    assert yaml.safe_load(undo.output)["archived_at"] is None


def test_experiment_archive_rejects_active_phase(
    runner: CliRunner, patched_cli, client, project, auth_headers
):
    exp_id = _create_experiment(client, auth_headers, project, title="exp-active-archive")

    result = runner.invoke(app, ["experiment", "archive", "--id", exp_id])

    assert result.exit_code == 1
    assert "complete or cancel" in result.output


# ---------------------------------------------------------------------------
# error paths (guidance for topic; 404 friendly + typer validation elsewhere)
# ---------------------------------------------------------------------------


def test_archive_not_found_404_friendly_message(
    runner: CliRunner, patched_cli
):
    missing_topic = str(uuid.uuid4())
    result = runner.invoke(app, ["topic", "archive", "--id", missing_topic])
    _assert_topic_archive_guidance(result)

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
    # topic archive 无 --id 也统一走引导拒绝（--id 已 optional）
    result = runner.invoke(app, ["topic", "archive"])
    _assert_topic_archive_guidance(result)

    result2 = runner.invoke(app, ["experiment", "archive"])
    assert result2.exit_code != 0
    assert "--id is required" in result2.output


# ---------------------------------------------------------------------------
# permission matrix (reviewer persona) — topic side now uniform guidance
# ---------------------------------------------------------------------------


def test_archive_permission_matrix(
    runner: CliRunner,
    monkeypatch,
    patched_cli,
    patched_reviewer_cli,
    client,
    db_session,
    project,
    auth_headers,
    reviewer,
):
    """experiment 侧保留双 persona 归档/恢复；topic 侧双 persona 均引导拒绝。"""
    host_token = auth_headers["Authorization"].removeprefix("Bearer ")
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")

    monkeypatch.setenv("MAP_TOKEN", host_token)
    topic = _db_topic(db_session, project, title="perm-topic")
    exp_id = _create_cancelled_experiment(client, auth_headers, project, title="perm-exp")

    # host: topic archive 引导拒绝；experiment archive 成功
    r1 = runner.invoke(app, ["topic", "archive", "--id", str(topic.id)])
    _assert_topic_archive_guidance(r1)
    r2 = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert r2.exit_code == 0, r2.output

    # reviewer: topic undo 引导拒绝（persona 无关）；experiment undo 被
    # creator-or-admin 门禁 403（authz PR3：PATCH 实验元数据仅创建者/管理员）
    monkeypatch.setenv("MAP_TOKEN", reviewer_token)
    r3 = runner.invoke(app, ["topic", "archive", "--id", str(topic.id), "--undo"])
    _assert_topic_archive_guidance(r3, kind="archive-undo")
    r4 = runner.invoke(app, ["experiment", "archive", "--id", exp_id, "--undo"])
    assert r4.exit_code == 1, r4.output
    assert "creator or an admin" in r4.output


# ---------------------------------------------------------------------------
# show visibility after archive
# ---------------------------------------------------------------------------


def test_show_after_archive_still_visible(
    runner: CliRunner, patched_cli, client, db_session, project, auth_headers
):
    topic = _db_topic(db_session, project, title="show-after-archive")

    archive = runner.invoke(app, ["topic", "archive", "--id", str(topic.id)])
    _assert_topic_archive_guidance(archive)

    # 只读路径不回退：show 仍可读 DB 话题
    show = runner.invoke(app, ["topic", "show", "--id", str(topic.id)])
    assert show.exit_code == 0, show.output
    show_payload = yaml.safe_load(show.output)
    assert show_payload["id"] == str(topic.id)

    # experiment variant（实验域归档行为保留）
    exp_id = _create_cancelled_experiment(client, auth_headers, project, title="exp-show-after-archive")
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
# audit best-effort (criterion 13, experiment domain only):
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
    exp_id = _create_cancelled_experiment(client, auth_headers, project, title="audit-exp")

    r_e = runner.invoke(app, ["experiment", "archive", "--id", exp_id])
    assert r_e.exit_code == 0, r_e.output
    r_e2 = runner.invoke(app, ["experiment", "archive", "--id", exp_id, "--undo"])
    assert r_e2.exit_code == 0, r_e2.output

    exp_audit = client.get(
        "/api/v1/audit",
        params={"target_type": "experiment", "target_id": exp_id},
        headers=auth_headers,
    )
    if exp_audit.status_code == 200:
        exp_actions = [entry.get("action", "") for entry in exp_audit.json()]
        assert any("archived" in a or "unarchived" in a for a in exp_actions), (
            f"expected an archive audit event for experiment, got {exp_actions}"
        )
    else:
        pytest.xfail(
            f"audit endpoint returned exp={exp_audit.status_code}"
        )
