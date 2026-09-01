"""``map feedback`` — GitHub issue 链接生成（用户反馈渠道）。

验收口径：
- 裸 ``map feedback`` 离线生成预填 URL（不连 API、不解析 persona）
- 环境信息（map version / Python / OS / MAP server）自动附在 body
- bug / idea 两种模板；``--repo`` 覆盖默认仓库（私有 fork）
- 默认不拉起浏览器，``--open`` 才调 ``webbrowser.open``
- 旧四命令 stub 回归：依旧 exit 2，引导文案指向新命令
"""

from __future__ import annotations

import urllib.parse

import map_sdk
from typer.testing import CliRunner

from cli.commands.feedback import DEFAULT_FEEDBACK_REPO, feedback_app

runner = CliRunner()

_URL_PREFIX = f"https://github.com/{DEFAULT_FEEDBACK_REPO}/issues/new?"


def _first_url(output: str) -> str:
    return output.strip().splitlines()[0]


def _issue_params(url: str) -> dict[str, str]:
    assert url.startswith(_URL_PREFIX), f"unexpected URL: {url!r}"
    return {k: v[0] for k, v in urllib.parse.parse_qs(url[len(_URL_PREFIX):]).items()}


def test_bare_feedback_generates_prefilled_bug_url() -> None:
    result = runner.invoke(feedback_app, ["--title", "waker does not wake"])
    assert result.exit_code == 0, result.output
    params = _issue_params(_first_url(result.output))
    assert params["title"].startswith("[bug] waker does not wake")
    body = params["body"]
    assert "## Problem" in body
    assert f"map version: {map_sdk.__version__}" in body
    assert "Python: " in body
    assert "OS: " in body
    assert "MAP server: " in body


def test_idea_type_uses_proposal_template() -> None:
    result = runner.invoke(
        feedback_app, ["--type", "idea", "--title", "add telemetry", "--body", "would help triage"]
    )
    assert result.exit_code == 0, result.output
    params = _issue_params(_first_url(result.output))
    assert params["title"].startswith("[idea] add telemetry")
    body = params["body"]
    assert "## Proposal" in body and "would help triage" in body
    assert "## Problem" not in body


def test_default_title_placeholder_when_missing() -> None:
    result = runner.invoke(feedback_app, [])
    assert result.exit_code == 0, result.output
    params = _issue_params(_first_url(result.output))
    assert params["title"] == "[bug] <short title>"


def test_repo_override_for_private_fork() -> None:
    result = runner.invoke(feedback_app, ["--repo", "someone/fork"])
    assert result.exit_code == 0, result.output
    assert _first_url(result.output).startswith("https://github.com/someone/fork/issues/new?")


def test_open_browser_only_with_flag(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "cli.commands.feedback.webbrowser.open", lambda url: opened.append(url) or True
    )

    result = runner.invoke(feedback_app, [])
    assert result.exit_code == 0, result.output
    assert opened == []  # 默认只打印 URL（Agent 主用场景不开浏览器）

    result = runner.invoke(feedback_app, ["--open"])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1 and opened[0].startswith(_URL_PREFIX)


def test_registered_on_main_app() -> None:
    """整链路：``map feedback`` 挂在主 app 上，callback 与 stub 子命令共存。"""
    from cli.main import app

    result = runner.invoke(app, ["feedback", "--title", "smoke"])
    assert result.exit_code == 0, result.output
    assert _URL_PREFIX in result.output


def test_legacy_stub_subcommands_still_exit_2() -> None:
    cases = [
        ["submit", "--body", "x"],
        ["list"],
        ["get", "some-uuid"],
        ["update", "some-uuid", "--status", "resolved"],
    ]
    for args in cases:
        result = runner.invoke(feedback_app, args)
        assert result.exit_code == 2, f"`map feedback {args[0]}` must stay retired, got {result.exit_code}"
