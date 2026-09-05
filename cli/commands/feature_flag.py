"""``map project config flag ...`` sub-app（实验 M2 I4：A4）。

挂载在 ``project_app`` 下，命名空间 ``map project config flag``。
三个命令：

- ``list`` —— 列出 project 下所有已 set 的 flag（默认 ``table``，
  ``--json`` 走信封格式）
- ``get --key <flag_key>`` —— 读单条 flag（不存在时非零退出 + 错误信
  息，避免静默「找不到也返 200」误导 caller）
- ``set --key <flag_key> --value on|off [--reason <text>]`` —— upsert。
  reason 在 ``on`` flip 时强制非空（kill switch / fail-closed gate
  flip 必须有审计锚点）；``off`` flip 允许空 reason（fast rollback
  不该被空文本阻塞）。

权限：set 必须 host creator / admin（服务端 ``feature_flag_service
._ensure_can_set_flag`` 二次 gate）。CLI 不预判权限——让 server 401
/403 路径透传给 caller，避免 SDK 与 server 权限矩阵漂移。
"""
from __future__ import annotations

import uuid

import typer
from map_client.client import MAPClient
from map_types.schemas import (
    FLAG_FS_STOP_DUPLICATE_INSERT,
    ProjectFeatureFlagSet,
)

from cli import runner  # module ref: test monkeypatch surface (T23)

flag_app = typer.Typer(help="Project feature flag commands (实验 M2 A4)")

# 把 flag_app 挂到 project_app.config 子命名空间下，让 ``map project
# config flag list`` 这条路径走通。project_app 顶层已有，config 子 app
# 是新加的——见 cli/commands/project.py 的 register()。


@flag_app.command("list")
def feature_flag_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """List project feature flags."""

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        return c.list_project_feature_flags(pid)

    runner._run(action)


@flag_app.command("get")
def feature_flag_get(
    flag_key: str = typer.Option(
        ...,
        "--key",
        help=(
            "Flag key. Currently only 'fs_stop_duplicate_insert' is "
            "registered (实验 M2 A4)."
        ),
    ),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Read a single project feature flag (404 if not set)."""

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        result = c.get_project_feature_flag(pid, flag_key)
        if result is None:
            # 走 stderr + 非零退出（runner 会把异常转 envelope）。
            raise typer.Exit(2)
        return result

    runner._run(action)


@flag_app.command("set")
def feature_flag_set(
    flag_key: str = typer.Option(
        ...,
        "--key",
        help="Flag key (currently 'fs_stop_duplicate_insert').",
    ),
    value: str = typer.Option(
        ...,
        "--value",
        help="Flag value. Accepted: on | off.",
    ),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help=(
            "Audit-anchor rationale. Required (non-empty) when flipping "
            "'on' for fs_stop_duplicate_insert (kill switch / fail-closed "
            "decision). Optional for 'off' (fast rollback)."
        ),
    ),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Set / flip a project feature flag."""

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        payload = ProjectFeatureFlagSet(flag_value=value, reason=reason)
        return c.set_project_feature_flag(pid, flag_key, payload)

    runner._run(action)


# 暴露本子 app 与注册用常量
__all__ = [
    "FLAG_FS_STOP_DUPLICATE_INSERT",
    "flag_app",
]
