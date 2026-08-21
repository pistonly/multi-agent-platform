"""反射校验 ``_is_write_command`` 覆盖了 ``cli/main.py`` 的全部写命令。

背景：``map --dry-run``（waker 的安全网）依赖 ``_is_write_command`` 判定
哪些子命令会修改 MAP 状态、必须跳过。白名单一旦漏登记，dry-run 就会
真实执行写操作（例如 ``action mark-wake-sent`` / ``mention dismiss``）。

本测试扫描 ``main.py`` 注册的每一个子组命令，强制它要么在写白名单里
（``_is_write_command`` 返回 True），要么在下方 ``_READ_ONLY_COMMANDS``
只读集合里——二者皆非则失败，提示开发者补登记。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.map_command_client import _is_write_command

MAIN_PY = Path(__file__).resolve().parents[2] / "cli" / "main.py"
COMMANDS_DIR = Path(__file__).resolve().parents[2] / "cli" / "commands"
E2E_COLLAB_PY = Path(__file__).resolve().parents[2] / "cli" / "e2e_collab.py"

# sub-app 变量名 → 命令路径前缀（与 cli/main.py 的 ``app.add_typer(..., name=...)``
# 及各 commands/*.py 内的嵌套 ``add_typer`` 对应）。
# 新增 sub-app 必须在此登记，否则 test_every_subgroup_command_is_classified 会失败。
_APP_VAR_TO_PATH: dict[str, tuple[str, ...]] = {
    "project_app": ("project",),
    "experiment_app": ("experiment",),
    "persona_app": ("persona",),
    "runtime_app": ("runtime",),
    "agent_app": ("agent",),
    "status_app": ("project", "status"),
    "lock_app": ("experiment", "lock"),
    "review_app": ("experiment", "review"),
    "plan_app": ("experiment", "plan"),
    "notification_app": ("notification",),
    "inbound_event_app": ("inbound-event",),
    "topic_app": ("topic",),
    "mention_app": ("mention",),
    "todo_app": ("todo",),
    "action_app": ("action",),
    "feedback_app": ("feedback",),
    "fs_app": ("fs",),
    "audit_app": ("audit",),
    "docs_app": ("docs",),
    "e2e_app": ("e2e",),
    "sync_app": ("sync",),
    "skill_app": ("skill",),
    "host_app": ("host",),
    "auth_app": ("auth",),
}

# 已知只读子组命令（不写 MAP 状态）。_is_write_command 必须对其返回 False。
# 写命令不在此处——它们由 _WRITE_COMMANDS_2 / _WRITE_COMMANDS_3 覆盖。
_READ_ONLY_COMMANDS: set[tuple[str, ...]] = {
    ("action", "list"),
    ("experiment", "list"),
    ("experiment", "show"),
    ("experiment", "status"),
    ("experiment", "logs"),
    ("experiment", "pre-complete"),
    ("feedback", "get"),
    ("feedback", "list"),
    ("mention", "list"),
    ("notification", "list"),
    ("persona", "list"),
    ("persona", "whoami"),
    ("topic", "list"),
    ("topic", "show"),
    ("topic", "progress"),
    ("runtime", "chat"),
    ("runtime", "status"),
    ("project", "list"),
    ("project", "decisions"),
    ("experiment", "review", "list"),
    ("experiment", "plan", "validate"),
    ("project", "status", "show"),
    ("project", "status", "versions"),
    ("audit", "list"),
    ("docs", "error-codes"),
    # 369ccac 拆分后补登记（此前绕过 dry-run 分类）
    ("agent", "list"),
    ("agent", "show"),
    ("agent", "escalation-target"),
    ("project", "export"),
    ("skill", "list"),
    ("skill", "install"),  # 只写本地文件（.cursor/skills/），不改 MAP 状态
    # M52A：upgrade 同 install，仅覆写本地 Skill 目录（可 diff 摘要预览）
    ("skill", "upgrade"),
    # fs plane 离线命令：只写本地 map/ 文件夹（事实源本身），不走 API。
    # advance-round / close 是验证型写（走 API），登记在 _WRITE_COMMANDS_2。
    ("fs", "init"),
    ("fs", "topic-create"),
    ("fs", "comment"),
    ("fs", "list"),
    ("fs", "show"),
    ("fs", "work"),
    ("fs", "migrate-from-docs"),
    # 部署矩阵握手：只读（本地扫描 + GET /fs/status）。
    ("fs", "status"),
    ("fs", "diff"),
    ("sync", "pull"),
    ("sync", "status"),
    ("sync", "topic"),
    ("sync", "topics"),
}

# 同时匹配单行 `@x_app.command("name")` 与多行 `@x_app.command(\n  "name",`。
_COMMAND_RE = re.compile(r'@(\w+_app)\.command\(\s*"([\w-]+)"')


def _scan_subgroup_commands() -> list[tuple[str, ...]]:
    """扫描 cli/main.py + cli/commands/*.py + cli/e2e_collab.py 的全部子命令。

    369ccac 拆分后子命令装饰器已从 main.py 迁至 cli/commands/*.py；
    只扫 main.py 会得到空集（反射正则失效但无人察觉）。
    """
    files = [MAIN_PY, *sorted(COMMANDS_DIR.glob("*.py")), E2E_COLLAB_PY]
    paths: list[tuple[str, ...]] = []
    for fp in files:
        text = fp.read_text(encoding="utf-8")
        for var, name in _COMMAND_RE.findall(text):
            prefix = _APP_VAR_TO_PATH.get(var)
            assert prefix is not None, (
                f"{fp.name} 用 @{var}.command 注册命令，但 _APP_VAR_TO_PATH 未登记该 sub-app，"
                "请在 test_dry_run_write_commands.py 补充映射。"
            )
            paths.append(prefix + (name,))
    return paths


def test_every_subgroup_command_is_classified() -> None:
    """每个子组命令必须被归为 write 或 read-only，否则新增命令会被无声漏掉。"""
    commands = set(_scan_subgroup_commands())
    assert commands, "未扫描到任何命令，反射正则可能已失效"

    unclassified = sorted(
        path for path in commands
        if not _is_write_command(list(path)) and path not in _READ_ONLY_COMMANDS
    )
    assert not unclassified, (
        "以下命令既不在 _is_write_command 白名单，也不在 _READ_ONLY_COMMANDS：\n  "
        + "\n  ".join(" ".join(p) for p in unclassified)
        + "\n→ 写命令加到 cli/map_command_client.py 的 _WRITE_COMMANDS_*，"
        "只读命令加到本测试的 _READ_ONLY_COMMANDS。"
    )


def test_read_only_commands_are_not_flagged_as_write() -> None:
    """只读命令不应被 _is_write_command 误判为写（互斥校验）。"""
    real = set(_scan_subgroup_commands())
    misclassified = sorted(
        path for path in _READ_ONLY_COMMANDS
        if path in real and _is_write_command(list(path))
    )
    assert not misclassified, (
        "以下 read-only 命令被 _is_write_command 错误标为写：\n  "
        + "\n  ".join(" ".join(p) for p in misclassified)
    )


@pytest.mark.parametrize(
    "args",
    [
        # 本次补全的、此前会绕过 dry-run 的危险写命令（回归保护）。
        ["action", "complete", "--id", "x"],
        ["action", "cancel", "--id", "x", "--reason", "r"],
        ["action", "link", "--id", "x", "--experiment-id", "y"],
        ["action", "mark-wake-sent", "--id", "x"],
        ["action", "mark-stale", "--id", "x"],
        ["mention", "dismiss", "--id", "x"],
        ["mention", "dismiss-all"],
        ["mention", "reconcile-stale"],
        ["notification", "read", "--id", "x"],
        ["notification", "read-all"],
        ["topic", "archive", "--id", "x"],
        ["topic", "read", "--id", "x"],
        ["topic", "mark-seen", "--id", "x"],
        ["experiment", "archive", "--id", "x"],
        ["experiment", "comment", "--id", "x", "--body", "b"],
        ["experiment", "accept-result", "--id", "x"],
        ["experiment", "reject-result", "--id", "x"],
        ["feedback", "submit"],
        ["feedback", "update", "--id", "x"],
        ["project", "create", "--key", "k", "--name", "n"],
        ["project", "status", "revise", "--file", "f"],
        ["todo", "clear", "--key", "k"],
        ["experiment", "review", "withdraw", "--id", "x"],
        # 原有写命令（确保重构未回归）。
        ["topic", "resolve", "--id", "x"],
        ["experiment", "complete", "--id", "x", "--summary", "s", "--file", "f"],
        ["experiment", "lock", "acquire", "--id", "x", "--ttl", "60"],
        ["inbound-event", "record", "--event-id", "e", "--fingerprint", "p"],
    ],
)
def test_known_write_command_is_flagged(args: list[str]) -> None:
    assert _is_write_command(args) is True, f"写命令未被识别: {args}"


def test_read_commands_default_to_false() -> None:
    assert _is_write_command([]) is False
    assert _is_write_command(["persona", "whoami"]) is False
    assert _is_write_command(["topic", "list", "--status", "open"]) is False
    assert _is_write_command(["experiment", "logs", "--id", "x"]) is False
    assert _is_write_command(["todos"]) is False
    assert _is_write_command(["work"]) is False


def test_subprocess_timeout_raises_worker_error(monkeypatch) -> None:
    """BUG #2a：subprocess 挂起时必须抛 WorkerError，而非永久阻塞 waker cycle。"""
    import subprocess as _sp

    from cli.host_worker_types import WorkerError
    from cli.map_command_client import MapCommandClient

    def _hang(*args: object, **kwargs: object) -> None:
        raise _sp.TimeoutExpired(cmd="map", timeout=0.01)

    monkeypatch.setattr("cli.map_command_client.subprocess.run", _hang)
    client = MapCommandClient(persona="host", dry_run=False, cmd_timeout=0.01)
    with pytest.raises(WorkerError, match="timed out"):
        client.whoami()
