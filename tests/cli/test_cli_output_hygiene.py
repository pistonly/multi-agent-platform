"""CLI 输出层卫生（实验 a6659021 / 话题 cli-output-format-consistency）。

A1 后置 ``--json`` 指引：任意 leaf 后置 ``--json`` 仍 exit 2，但错误后追加
全局选项位置指引（``map --json <cmd>`` / ``--format json``）；id 域命令不再
返回误导性的「你是不是想用 --id」；其他拼写错误仍走 did-you-mean。
A2 纯 JSON 防回归：前置 ``--json`` 与 per-command ``--format json`` 的 stderr
envelope 首行即 JSON（无 markdown / 人类可读前缀）。``map --json work`` 的
端到端纯 JSON 由实验段二实测留证（work 读本地 FS + API，不适合 CI 内存态）。
A3 ``_render_topic_table``：closed/archived 话题 ROUND 列 ``-``，open 不受影响。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from map_client.exceptions import MAPNotFoundError
from typer.testing import CliRunner

import cli.main as cli_main
from cli.commands.topic import _render_topic_table
from cli.main import app

_JSON_HINT = "--json 是全局选项"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli_maphttp_error(monkeypatch):
    """Transport raises MAPNotFoundError(404) on every call."""

    class _RaisingTransport:
        def handle_request(self, request):
            raise MAPNotFoundError(404, "Experiment not found")

    monkeypatch.setattr(cli_main, "_transport", _RaisingTransport())
    monkeypatch.setenv("MAP_TOKEN", "fake")
    monkeypatch.setenv("MAP_API_URL", "http://test")


# ---- A1: 后置 --json 的位置指引 --------------------------------------------


def test_post_json_hint_on_plain_leaf(runner):
    """``map work --json``：无 id/topic 选项的 leaf 也给位置指引。"""
    result = runner.invoke(app, ["work", "--json"])
    assert result.exit_code == 2
    assert "No such option" in result.output
    assert _JSON_HINT in result.output
    assert "map --json" in result.output
    assert "--format json" in result.output


def test_post_json_hint_on_topic_list(runner):
    result = runner.invoke(app, ["topic", "list", "--json"])
    assert result.exit_code == 2
    assert _JSON_HINT in result.output


def test_post_json_hint_wins_over_did_you_mean_on_id_domain(runner):
    """id 域命令后置 ``--json`` 给位置指引，而不是「你是不是想用 --id」。"""
    result = runner.invoke(app, ["experiment", "status", "--json"])
    assert result.exit_code == 2
    assert _JSON_HINT in result.output
    assert "你是不是想用" not in result.output


def test_misspelled_option_still_did_you_mean(runner):
    """其他拼写错误不受 A1 影响，仍走 did-you-mean。"""
    result = runner.invoke(app, ["experiment", "status", "--topc"])
    assert result.exit_code == 2
    assert "你是不是想用" in result.output
    assert _JSON_HINT not in result.output


# ---- A2: 前置 --json / --format json 纯 envelope 防回归 ---------------------


def _stderr_first_line(result) -> str:
    return next(ln for ln in (result.stderr or "").splitlines() if ln.strip())


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "experiment", "show", "--id"],
        ["experiment", "show", "--format", "json", "--id"],
    ],
)
def test_json_envelope_has_no_human_prefix(runner, patched_cli_maphttp_error, monkeypatch, argv):
    """stderr 首行即 JSON envelope——无 markdown / 人类可读渲染前缀。"""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    result = runner.invoke(app, [*argv, str(uuid.uuid4())])
    assert result.exit_code != 0
    first = _stderr_first_line(result)
    assert first.lstrip().startswith("{"), result.stderr
    envelope = json.loads(first)
    assert envelope["ok"] is False


# ---- A3: topic list ROUND 列 ------------------------------------------------


def _topic(status: str, discussion_round: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="某话题",
        status=status,
        discussion_round=discussion_round,
        comment_count=0,
        experiment_count=0,
        creator_name="host",
        created_at=None,
    )


def test_round_column_dash_for_closed_and_archived():
    table = _render_topic_table([_topic("closed", "ready"), _topic("archived", "round3")])
    lines = table.splitlines()
    closed_row = next(ln for ln in lines if "closed" in ln)
    archived_row = next(ln for ln in lines if "archived" in ln)
    for row in (closed_row, archived_row):
        assert " - " in row
    assert "ready" not in closed_row
    assert "round3" not in archived_row


def test_round_column_keeps_round_state_for_open():
    table = _render_topic_table([_topic("open", "round1"), _topic("open", "ready")])
    assert "round1" in table
    assert "ready" in table
    # 表头保持 ROUND 不改名
    assert table.splitlines()[0].split()[3] == "ROUND"
