"""Unit tests for ``map bootstrap`` 409/已存在分流（3b7c2b44 A3）。

按用户意图三条出路：丢 token → reissue；config 陈旧 → heal；想整体重做 →
archive/换 key。覆盖本地 already-exists 的 ValueError 文案与 CLI server 409
catch 两条路径。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from map_client.bootstrap import bootstrap_conflict_triage, bootstrap_project_map
from typer.testing import CliRunner


def test_triage_message_lists_three_intents() -> None:
    msg = bootstrap_conflict_triage("demo-key")
    assert "按意图分流" in msg
    assert "reissue" in msg and "--heal" in msg and "archive" in msg


def test_bootstrap_local_exists_valueerror_includes_triage(tmp_path: Path) -> None:
    (tmp_path / ".map").mkdir(parents=True)
    (tmp_path / ".map" / "agents.local.yaml").write_text(
        yaml.safe_dump({"personas": {"host": {"token": "x"}}}), encoding="utf-8"
    )
    with pytest.raises(ValueError) as exc_info:
        bootstrap_project_map(
            project_key="demo-key",
            project_name="Demo",
            workspace_path=tmp_path,
            project_root=tmp_path,
        )
    assert "按意图分流" in str(exc_info.value)


def test_bootstrap_server_409_cli_prints_triage(
    tmp_path: Path, monkeypatch
) -> None:
    import cli.main as main

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "project_key already exists"})

    monkeypatch.setattr(
        main, "_transport", httpx.MockTransport(handler)
    )
    # 空 project-root：无 .map/，路径走 server 自服务 409
    result = CliRunner().invoke(
        main.app,
        ["bootstrap", "--key", "demo-key", "--name", "Demo", "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "按意图分流" in result.output
    assert "reissue" in result.output and "--heal" in result.output
