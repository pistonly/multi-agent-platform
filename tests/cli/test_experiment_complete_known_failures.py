"""50cddb7e I4 (A3) — CLI ``experiment complete --known-failures`` 接线 E2E。

``test_pytest_summary_gate.py`` 直接打 server 层验证了门禁语义;本文件走完整
CLI 端到端路径(``runner.invoke(app, ...)`` + ``MAPTestClientTransport``),
钉住 :class:`~cli.commands.experiment.experiment_complete` 的
``--known-failures`` 透传:

* red ``pytest_summary`` + 重复 ``--known-failures`` → 豁免放行,completion log
  打上 ``exempted`` 标记(证明 flag 一路到达 service 层校验)。
* red ``pytest_summary`` 不带豁免 → CLI 报 actionable 错误、phase 保持 running。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from map_client import project_config
from map_client.testing import MAPTestClientTransport
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from tests._frontmatter import make_valid_plan

pytestmark = pytest.mark.slow

_RED = {"total": 35, "passed": 30, "failed": 5}


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
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )


def _make_running_experiment(client, auth_headers, reviewer, project) -> str:
    exp_id = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "known-failures CLI",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
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
    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    started = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    assert started.status_code == 200
    return exp_id


def _write_files(tmp_path: Path):
    log = tmp_path / "log.md"
    log.write_text("## 实施 log\n基线验证完成。\n## 风险\n无。\n", encoding="utf-8")
    meta = tmp_path / "meta.yaml"
    meta.write_text("pytest_summary: {total: 35, passed: 30, failed: 5}\n", encoding="utf-8")
    return log, meta


def test_complete_known_failures_waives_red_and_lands_exempted_marker(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path
):
    exp_id = _make_running_experiment(client, auth_headers, reviewer, project)
    log, meta = _write_files(tmp_path)

    result = runner.invoke(
        app,
        [
            "experiment",
            "complete",
            "--id",
            str(uuid.UUID(exp_id)),
            "--summary",
            "trial complete",
            "--file",
            str(log),
            "--metadata",
            str(meta),
            "--known-failures",
            "#42",
            "--known-failures",
            "test_zz_slow",
        ],
    )
    assert result.exit_code == 0, result.output

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["phase"] == "result_review"
    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers).json()
    marker = logs[-1]["metadata_json"]["pytest_summary_validation"]
    assert marker["status"] == "exempted"
    assert marker["known_failures"] == ["#42", "test_zz_slow"]


def test_complete_red_without_exemption_rejects_via_cli(
    runner, patched_cli, client, auth_headers, reviewer, project, tmp_path
):
    exp_id = _make_running_experiment(client, auth_headers, reviewer, project)
    log, meta = _write_files(tmp_path)

    result = runner.invoke(
        app,
        [
            "experiment",
            "complete",
            "--id",
            str(uuid.UUID(exp_id)),
            "--summary",
            "trial complete",
            "--file",
            str(log),
            "--metadata",
            str(meta),
        ],
    )
    assert result.exit_code != 0, result.output
    assert "complete 被拒" in result.output
    assert "--known-failures" in result.output

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["phase"] == "running"
