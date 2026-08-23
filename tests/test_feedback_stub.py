"""v0.15 M62：``map feedback`` 四命令退役 stub 行为（死信箱拆除）。

验收口径（话题 v015-feedback-deprecation-design round2 定稿）：
- 四命令统一 exit 2 引导（M58 ``_DB_WRITE_RETIRED`` 同款模式）
- 文案两行分流：bug → GitHub issue；改进想法 → host 开 MAP 话题
- 含「历史数据只读保留」提示（防 Agent 误判数据被删而恐慌性重提）
- 服务端 4 端点 410 + 引导 body（API 消费者的第二道门）
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from cli.commands.feedback import _HINT
from server.main import app

client = TestClient(app)


def _run_cli(capsys, fn, *args, **kwargs) -> int:
    """进程内调用 stub 命令，捕获输出与退出码。"""
    import pytest
    import typer

    try:
        fn(*args, **kwargs)
    except typer.Exit as exc:
        captured = capsys.readouterr()
        return exc.exit_code or 0, captured.err
    raise pytest.fail("stub must raise typer.Exit(2)")


def test_stub_submit_exit2_with_guide(capsys) -> None:
    from cli.commands.feedback import feedback_submit

    code, err = _run_cli(capsys, feedback_submit, body="x")
    assert code == 2
    assert "retired" in err and "v0.15 M62" in err
    # 两行分流 + 只读保留提示
    assert "GitHub issue" in err
    assert "MAP topic" in err
    assert "read-only" in err


def test_stub_admin_triage_exit2(capsys) -> None:
    from cli.commands.feedback import feedback_get, feedback_list, feedback_update

    code, _ = _run_cli(capsys, feedback_list, status="new")
    assert code == 2
    code, _ = _run_cli(capsys, feedback_get, "some-uuid")
    assert code == 2
    code, _ = _run_cli(capsys, feedback_update, "some-uuid", status="resolved")
    assert code == 2


def test_hint_channels_and_readonly_note() -> None:
    assert "GitHub issue" in _HINT and "MAP topic" in _HINT
    assert "read-only" in _HINT


def test_api_four_endpoints_410_with_guide(client, auth_headers) -> None:
    for method, path in [
        ("post", "/api/v1/feedback"),
        ("get", "/api/v1/feedback"),
        ("get", "/api/v1/feedback/00000000-0000-0000-0000-000000000000"),
        ("patch", "/api/v1/feedback/00000000-0000-0000-0000-000000000000"),
    ]:
        kwargs: dict = {"headers": auth_headers}
        if method in ("post", "patch"):
            kwargs["json"] = {}
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 410, f"{method} {path} -> {resp.status_code}"
        detail = resp.json()["detail"]
        assert detail["error"] == "feedback_retired"
        assert "GitHub issue" in detail["hint"] and "MAP topic" in detail["hint"]


def test_orm_and_data_preserved_readonly() -> None:
    """表与历史数据只读保留（M58 先例）：ORM 模型与 enums 存在，11 条 resolved 可查。"""
    import sqlite3
    from pathlib import Path

    from map_types.enums import FeedbackCategory, FeedbackStatus  # noqa: F401

    from server.domain.models import PlatformFeedback  # noqa: F401

    db = Path(__file__).resolve().parents[1] / "data" / "map.db"
    if not db.exists():  # CI 环境无本地 DB 时跳过数据断言，只验 import
        return
    con = sqlite3.connect(db)
    rows = con.execute("SELECT status, COUNT(*) FROM platform_feedback GROUP BY status").fetchall()
    con.close()
    assert dict(rows).get("resolved", 0) >= 1  # M62-a 清账后至少 1 条 resolved
