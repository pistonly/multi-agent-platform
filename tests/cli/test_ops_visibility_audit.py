"""ops-visibility-batch（4770ea76）：audit CLI 双入口五组单测。

1. --target 解析（话题/实验 slug 与 uuid、错 slug、撞名）
2. topic history 聚合排序（话题事件 + 实验事件交错）
3. 权限拒绝
4. 三种格式
5. 空结果友好提示
另：C4 无 --target 仍走 admin 全局
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from map_client.testing import MAPTestClientTransport
from map_fs import topic_id_for_slug, write_topic_index
from map_types.enums import TopicStatus
from sqlalchemy import select
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from server.domain.models import AuditLog, Experiment, Topic
from server.services import audit_service
from tests._frontmatter import make_valid_plan

runner = CliRunner()


@pytest.fixture
def patched_persona_cli(monkeypatch, client, auth_headers, tmp_path: Path) -> Path:
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://test\nproject_key: test-project\ndefault_persona: host\n",
        encoding="utf-8",
    )
    (map_dir / "agents.yaml").write_text(
        "personas:\n  host:\n    agent_name: test-agent\n",
        encoding="utf-8",
    )
    (map_dir / "agents.local.yaml").write_text(
        f"personas:\n  host:\n    token: {token}\n    agent_name: test-agent\n",
        encoding="utf-8",
    )
    return tmp_path


def _fs_aligned_topic(db_session, *, project_id, creator_agent_id, slug: str, title: str):
    topic = Topic(
        id=topic_id_for_slug(slug),
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        title=title,
        slug=slug,
        status=TopicStatus.open,
        discussion_round="round1",
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _create_experiment(client, auth_headers, project, *, title: str, topic_id=None):
    payload = {
        "title": title,
        "plan": {"content_md": make_valid_plan(title=title, body="## plan")},
        "submit_for_review": False,
    }
    if topic_id is not None:
        payload["topic_id"] = str(topic_id)
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. --target 解析
# ---------------------------------------------------------------------------


def test_target_resolves_experiment_uuid(
    patched_persona_cli, client, auth_headers, project
):
    exp = _create_experiment(client, auth_headers, project, title="audit-target-exp")
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "audit",
            "list",
            "--target",
            exp["id"],
        ],
    )
    assert result.exit_code == 0, result.output
    assert "experiment.created" in result.output
    assert "TIME" in result.output and "ACTION" in result.output


def test_target_resolves_experiment_shortid(
    patched_persona_cli, client, auth_headers, project
):
    exp = _create_experiment(client, auth_headers, project, title="shortid-exp")
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "audit",
            "list",
            "--target",
            exp["id"][:8],
        ],
    )
    assert result.exit_code == 0, result.output
    assert "experiment.created" in result.output


def test_target_resolves_topic_slug(
    patched_persona_cli, client, auth_headers, project, db_session, agent_token
):
    write_topic_index(patched_persona_cli, "vis-topic", title="Vis", creator="host")
    agent_id = uuid.UUID(agent_token[0])
    topic = _fs_aligned_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=agent_id,
        slug="vis-topic",
        title="Vis",
    )
    db_session.commit()
    audit_service.log_no_commit(
        db_session,
        action="topic.closed",
        target_type="topic",
        target_id=topic.id,
        agent_id=agent_id,
        project_id=uuid.UUID(project["id"]),
        summary="fixture close",
    )
    db_session.commit()
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "audit",
            "list",
            "--target",
            "vis-topic",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "topic.closed" in result.output


def test_target_unknown_slug_friendly_error(patched_persona_cli):
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "audit",
            "list",
            "--target",
            "no-such-slug",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "no-such-slug" in result.output


def test_target_collision_lists_both(
    patched_persona_cli, client, auth_headers, project, db_session
):
    slug = "dup-target"
    write_topic_index(patched_persona_cli, slug, title="Dup", creator="host")
    exp_dir = patched_persona_cli / "map" / "experiments" / slug
    exp_dir.mkdir(parents=True)
    (exp_dir / "plan.md").write_text(make_valid_plan(title="dup"), encoding="utf-8")
    exp = _create_experiment(client, auth_headers, project, title="dup-exp")
    row = db_session.get(Experiment, uuid.UUID(exp["id"]))
    assert row is not None
    row.plan_file_path = f"map/experiments/{slug}/plan.md"
    db_session.commit()

    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "audit",
            "list",
            "--target",
            slug,
        ],
    )
    assert result.exit_code == 2, result.output
    assert "matches both" in result.output
    assert "experiment" in result.output
    assert "topic" in result.output


# ---------------------------------------------------------------------------
# 2. topic history 聚合排序
# ---------------------------------------------------------------------------


def test_topic_history_interleaves_topic_and_experiment_events(
    patched_persona_cli, client, auth_headers, project, db_session, agent_token
):
    slug = "hist-topic"
    write_topic_index(patched_persona_cli, slug, title="Hist", creator="host")
    agent_id = uuid.UUID(agent_token[0])
    topic = _fs_aligned_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=agent_id,
        slug=slug,
        title="Hist",
    )
    db_session.commit()
    _create_experiment(
        client, auth_headers, project, title="hist-exp", topic_id=topic.id
    )
    now = datetime.now(timezone.utc)
    audit_service.log_no_commit(
        db_session,
        action="topic.commented",
        target_type="topic",
        target_id=topic.id,
        agent_id=agent_id,
        project_id=uuid.UUID(project["id"]),
        summary="older topic event",
    )
    db_session.commit()
    topic_row = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "topic.commented", AuditLog.target_id == topic.id
        )
    )
    assert topic_row is not None
    topic_row.created_at = now - timedelta(hours=2)
    db_session.commit()

    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "topic",
            "history",
            "--id",
            slug,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "experiment.created" in result.output
    assert "topic.commented" in result.output
    created_pos = result.output.index("experiment.created")
    commented_pos = result.output.index("topic.commented")
    assert created_pos < commented_pos, result.output


# ---------------------------------------------------------------------------
# 3. 权限拒绝
# ---------------------------------------------------------------------------


def test_target_permission_denied_other_project(
    patched_persona_cli, client, admin_headers
):
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": "other-audit-proj",
            "name": "Other",
            "workspace_path": "/tmp/other-audit-proj",
        },
    )
    assert other.status_code == 201, other.text
    other_agent = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "other-audit-agent",
            "role": "agent",
            "project_key": "other-audit-proj",
        },
    )
    assert other_agent.status_code == 201, other_agent.text
    other_headers = {"Authorization": f"Bearer {other_agent.json()['api_token']}"}
    exp = client.post(
        f"/api/v1/projects/{other.json()['id']}/experiments",
        headers=other_headers,
        json={
            "title": "secret-exp",
            "plan": {"content_md": make_valid_plan(title="secret")},
        },
    )
    assert exp.status_code == 201, exp.text

    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "audit",
            "list",
            "--target",
            exp.json()["id"],
        ],
    )
    assert result.exit_code != 0, result.output
    assert (
        "403" in result.output
        or "Access denied" in result.output
        or "forbidden" in result.output.lower()
    )


# ---------------------------------------------------------------------------
# 4. 三种格式
# ---------------------------------------------------------------------------


def test_target_formats_table_yaml_json(
    patched_persona_cli, client, auth_headers, project
):
    exp = _create_experiment(client, auth_headers, project, title="fmt-exp")
    base = [
        "--project-root",
        str(patched_persona_cli),
        "audit",
        "list",
        "--target",
        exp["id"],
    ]

    table = runner.invoke(app, base)
    assert table.exit_code == 0, table.output
    assert "TIME" in table.output and "ACTION" in table.output

    yml = runner.invoke(app, base + ["--format", "yaml"])
    assert yml.exit_code == 0, yml.output
    rows = yaml.safe_load(yml.output)
    assert isinstance(rows, list) and rows
    assert rows[0]["action"] == "experiment.created"

    js = runner.invoke(app, base + ["--format", "json"])
    assert js.exit_code == 0, js.output
    payload = json.loads(js.output)
    assert isinstance(payload, list) and payload
    assert payload[0]["action"] == "experiment.created"


# ---------------------------------------------------------------------------
# 5. 空结果
# ---------------------------------------------------------------------------


def test_empty_target_friendly_message_not_empty_headers(
    patched_persona_cli, db_session, project, agent_token
):
    write_topic_index(patched_persona_cli, "empty-vis", title="Empty", creator="host")
    topic = _fs_aligned_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=uuid.UUID(agent_token[0]),
        slug="empty-vis",
        title="Empty",
    )
    db_session.commit()
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(patched_persona_cli),
            "audit",
            "list",
            "--target",
            "empty-vis",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No audit events" in result.output
    assert "TIME" not in result.output
    assert topic.id is not None


def test_c4_without_target_still_requires_admin(patched_persona_cli, monkeypatch):
    """无 --target 不走 GET /audit；继续 admin 全局路径。"""
    monkeypatch.delenv("MAP_ADMIN_TOKEN", raising=False)
    result = runner.invoke(
        app,
        ["--project-root", str(patched_persona_cli), "audit", "list"],
    )
    assert result.exit_code != 0
    assert "admin" in result.output.lower() or "token" in result.output.lower()


def test_audit_list_help_mentions_target():
    result = runner.invoke(app, ["audit", "list", "--help"])
    assert result.exit_code == 0, result.output
    assert "--target" in result.output


def test_topic_history_help_exists():
    result = runner.invoke(app, ["topic", "history", "--help"])
    assert result.exit_code == 0, result.output
    assert "--id" in result.output
