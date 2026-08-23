"""207d7c4b I2(A2+A6) — experiment 域 CLI 卫生:log 前置校验 + 收尾动线。

纯 CLI 层(CliRunner + stub transport,无 server):

* A2  ``experiment log --summary`` 缺内容文件(--file/--log-file-path 都没传)时,
      输出一行式 ``Error: --summary requires --file or --log-file-path``、exit 2,
      且**无 pydantic ValidationError 堆栈**、在发起任何请求前被拒绝。
* A6  ``experiment pre-complete`` 成功输出末尾回显可粘贴的 complete 命令行
      (--id/--metadata 按本次入参填好,--summary/--log-file-path 作显式占位)。
* A6  ``experiment complete`` 缺 --metadata(无 completion evidence)时,错误信息
      前置给出 accepted keys + 示例 JSON 片段(--schema 提示),不再只在失败后可见。
"""
from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from cli import main as cli_main
from cli.main import app

_EXP_ID = "11111111-2222-3333-4444-555555555555"
_TS = "2026-08-23T12:00:00+00:00"


class NoRequestTransport(httpx.BaseTransport):
    """记录所有发出的请求并全部 404——用于断言「任何请求发出前就被拒绝」的路径。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(404, json={"detail": "unstubbed"})


class PreCompleteTransport(httpx.BaseTransport):
    """只 stub ``GET /experiments/{id}``(pre-complete 唯一网络调用),其余 404。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path == f"/api/v1/experiments/{_EXP_ID}":
            return httpx.Response(
                200,
                json={
                    "id": _EXP_ID,
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "creator_agent_id": "00000000-0000-0000-0000-000000000002",
                    "title": "pre-complete echo",
                    "description": "hygiene test stub",
                    "phase": "draft",
                    "current_plan_version": 1,
                    "created_at": _TS,
                    "updated_at": _TS,
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


# --- A2: log --summary 缺内容文件 → 一行式 error(无 pydantic 堆栈) ------------


def test_log_summary_without_content_one_line_error(stub_env, runner) -> None:
    transport = NoRequestTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        ["experiment", "log", "--id", _EXP_ID, "--summary", "s"],
    )
    assert result.exit_code == 2
    assert "Error: --summary requires --file or --log-file-path" in result.output
    assert "ValidationError" not in result.output
    assert transport.requests == []  # 在发起任何请求前被拒绝


def test_log_with_file_still_allows_empty_transport_check(stub_env, runner, tmp_path) -> None:
    """回归护栏:给 --file 时不应被新前置误伤(依旧走既有构造,不再此触发)。"""
    f = tmp_path / "log.md"
    f.write_text("# body", encoding="utf-8")
    transport = NoRequestTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        ["experiment", "log", "--id", _EXP_ID, "--summary", "s", "--file", str(f)],
    )
    # 前置校验放行 → 走到 payload 构造 → stub 404(测试环境无 server)。核心断言
    # 是**不再触发 exit 2 的行式错误**,而是下沉到正常请求路径。
    assert "Error: --summary requires --file or --log-file-path" not in result.output
    assert transport.requests != []
    assert result.exit_code == 1


# --- A6: pre-complete 成功输出末尾回显 complete 命令行 ---------------------


def test_pre_complete_echoes_copy_paste_complete_command(stub_env, runner, tmp_path) -> None:
    md = tmp_path / "complete-evidence.yaml"
    md.write_text("api_health: ok\n", encoding="utf-8")
    transport = PreCompleteTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        ["experiment", "pre-complete", "--id", _EXP_ID, "--metadata", str(md)],
    )
    assert result.exit_code == 0, result.output
    # 末尾回显完整可粘贴命令行:--id/--metadata 按本次入参填好
    assert "Next (copy-paste, fill in <summary> and <log.md>)" in result.output
    assert f"experiment complete --id {_EXP_ID}" in result.output
    assert f"--metadata {md}" in result.output
    assert "--summary '<summary>' --log-file-path <log.md>" in result.output


# --- A6: complete 缺 metadata → error 前置 accepted keys + 示例 JSON ----------


def test_complete_missing_metadata_error_previews_keys_and_example(
    stub_env, runner, tmp_path
) -> None:
    log_f = tmp_path / "log.md"
    log_f.write_text("# body", encoding="utf-8")
    transport = NoRequestTransport()
    stub_env(transport)

    result = runner.invoke(
        app,
        [
            "experiment",
            "complete",
            "--id",
            _EXP_ID,
            "--summary",
            "s",
            "--file",
            str(log_f),
        ],
    )
    assert result.exit_code == 2
    # accepted keys 列表前置可见(不再只在失败后才知道要传什么)
    assert "accepted keys include:" in result.output
    assert "api_health" in result.output
    assert "pytest_summary" in result.output
    # 示例 JSON 片段
    assert '"api_health": "ok"' in result.output
    assert "experiment complete --schema" in result.output
    assert transport.requests == []  # 在发起任何请求前被拒绝
