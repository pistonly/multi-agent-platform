"""207d7c4b I3(A4+A5) — --topic-id slug 双路由 + fs 域 --id 别名 + did-you-mean。

纯 CLI 层(CliRunner + stub transport / help / 解析层,无 server):

* A4  ``experiment create --topic-id <slug>`` 把非 uuid 形态当作 FS 话题 slug
      解析为确定性 uuid5(topic_id_for_slug),直接成功无需先 ``topic show`` 抄
      uuid;uuid 直传路径不回归(payload.topic_id 原样透传)。
* A5  fs 域 --topic 命令注册 ``--id`` 双轨别名(help 层断言,不碰磁盘);
      参数解析失败时输出 did-you-mean:
        - 裸传位置 slug → ``你是不是想用 --id <slug>``
        - flag 拼错 → 近邻匹配到已知 option
"""
from __future__ import annotations

import json

import httpx
import pytest
from map_fs import topic_id_for_slug
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

_EXP_ID = "11111111-2222-3333-4444-555555555555"
_PROJECT_ID = "22222222-3333-4444-5555-666666666666"
_AGENT_ID = "33333333-4444-5555-6666-777777777777"
_TS = "2026-08-23T12:00:00+00:00"


class CreateStubTransport(httpx.BaseTransport):
    """capture POST /projects/{pid}/experiments body;其余 404。

    create 成功后会 ``GET /agents/me`` 取 creator persona（cli.experiment_fs
    写 index.md 用），一并桩掉。
    """

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/agents/me"):
            return httpx.Response(
                200,
                json={
                    "id": _AGENT_ID,
                    "name": "host-agent",
                    "role": "admin",
                    "project_id": _PROJECT_ID,
                    "created_at": _TS,
                },
            )
        if request.method == "POST" and request.url.path.endswith("/experiments"):
            self.bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": _EXP_ID,
                    "project_id": _PROJECT_ID,
                    "creator_agent_id": _AGENT_ID,
                    "title": json.loads(request.content).get("title", "t"),
                    "description": None,
                    "phase": "draft",
                    "mode": "standard",
                    "current_plan_version": 0,
                    "created_at": _TS,
                    "updated_at": _TS,
                },
            )
        return httpx.Response(404, json={"detail": "unstubbed"})


class NoRequestTransport(httpx.BaseTransport):
    """记录请求并全 404——did-you-mean 在解析层即退出,不应有请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(404, json={"detail": "unstubbed"})


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_options(monkeypatch):
    """防止前序测试的 --format 泄漏污染解析层断言。"""
    monkeypatch.setattr(
        cli_main,
        "_cli_options",
        {"persona": None, "project_root": None, "format": "yaml"},
    )


@pytest.fixture
def stub_env(monkeypatch, tmp_path):
    def _install(transport: httpx.BaseTransport) -> None:
        monkeypatch.setattr(cli_main, "_transport", transport)
        monkeypatch.setenv("MAP_TOKEN", "fake")
        monkeypatch.setenv("MAP_API_URL", "http://test")
        monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
        monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
        # experiment_fs 在模块加载时直接绑定 project_config.find_map_dir，
        # 只补丁 cli_main 不够——create 写回会污染真实 workspace
        # （曾写入 map/experiments/hygiene-a4/）。
        monkeypatch.setattr(
            "cli.experiment_fs.find_map_dir", lambda *args, **kwargs: None
        )

    return _install


# --- A4: --topic-id slug → uuid5 双路由 + uuid 透传不回归 -------------------


def _create_args(plan_file, topic_flag, topic_value, extra=None):
    args = [
        "experiment",
        "create",
        "--title",
        "hygiene A4",
        "--plan-file-path",
        str(plan_file),
        "--force-lint-bypass",
        "--project",
        _PROJECT_ID,
        topic_flag,
        topic_value,
    ]
    if extra:
        args.extend(extra)
    return args


def test_create_topic_id_slug_resolves_to_uuid5(stub_env, runner, tmp_path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("# plan body", encoding="utf-8")
    transport = CreateStubTransport()
    stub_env(transport)

    result = runner.invoke(app, _create_args(plan, "--topic-id", "my-slug"))
    assert result.exit_code == 0, result.output
    assert len(transport.bodies) == 1
    body = transport.bodies[0]
    assert body["topic_id"] == str(topic_id_for_slug("my-slug"))


def test_create_topic_id_uuid_passthrough(stub_env, runner, tmp_path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("# plan body", encoding="utf-8")
    transport = CreateStubTransport()
    stub_env(transport)
    topic_uuid = "44444444-5555-6666-7777-888888888888"

    result = runner.invoke(app, _create_args(plan, "--topic-id", topic_uuid))
    assert result.exit_code == 0, result.output
    body = transport.bodies[0]
    assert body["topic_id"] == topic_uuid  # DB 话题 uuid 直传,不经过 uuid5 映射


# --- A5: fs 域 --id 双轨别名 ------------------------------------------------


def test_fs_comment_id_alias_registered(runner) -> None:
    """T2-P2:--topic 命令注册 --id 双轨别名(--topic 不 break)。"""
    result = runner.invoke(app, ["fs", "comment", "--help"])
    assert result.exit_code == 0
    # 类型占位渲染(TEXT / <str>)随 click 版本不定,前缀断言三连已足够
    assert "--topic, --id" in result.output


# --- A5: did-you-mean(解析层)------------------------------------------------


def test_extra_argument_did_you_mean(stub_env, runner) -> None:
    """required --id 缺失时裸传位置 slug(ex:``map experiment status myslug``)。

    vendored click 报 ``Missing parameter: experiment_id``(param 名非 flag),did-you-mean
    反查 flags 后提示 ``你是不是想用 --id <slug>``(验收字面形态)。
    """
    transport = NoRequestTransport()
    stub_env(transport)

    result = runner.invoke(app, ["experiment", "status", "myslug"])
    assert result.exit_code == 2
    assert "你是不是想用 --id <slug>" in result.output
    assert transport.requests == []  # 解析层即退出,零请求


def test_no_such_option_did_you_mean(stub_env, runner) -> None:
    """flag 拼错(ex:``--sumary``)→ 近邻匹配到已知 option。"""
    transport = NoRequestTransport()
    stub_env(transport)

    result = runner.invoke(app, ["experiment", "log", "--sumary", "s"])
    assert result.exit_code == 2
    assert "你是不是想用 --summary" in result.output
    assert transport.requests == []
