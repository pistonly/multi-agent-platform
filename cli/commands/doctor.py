"""``map doctor ...`` sub-app — config-versus-authority diagnostics (3b7c2b44 A1).

对比 ``.map/`` 本地标识（project_id / agents）与服务端权威：分叉项列清单
并给修复命令；``--check`` 供 CI 机器判定。
"""
from __future__ import annotations

from pathlib import Path

import typer
from map_client.exceptions import MAPHTTPError
from map_client.project_config import CONFIG_FILE, load_project_map_config

# --check exit codes（plan 3b7c2b44 A1，判定口径定死）：
EXIT_CLEAN = 0        # 无分叉
EXIT_DIVERGED = 1     # 存在可修复配置分叉（CI 红灯）
EXIT_DIAGNOSTIC = 2   # 诊断自身异常（权威解析失败/网络/本地标识缺失等，无法判定）

doctor_app = typer.Typer(help="MAP 诊断（config 对账等）")


def inspect_config_divergences(
    *,
    project_root: Path | None = None,
    client=None,
) -> tuple[list[tuple[str, str]], int]:
    """返回 (分叉项 [(类别, 描述)], exit code)。

    不抛异常：任何无法判定的情况归为 EXIT_DIAGNOSTIC。``client`` 供测试注入。
    """
    divergences: list[tuple[str, str]] = []
    try:
        cfg = load_project_map_config(project_root=project_root)
    except ValueError as exc:
        return [], _diagnostic(str(exc))
    if cfg.project_id is None:
        divergences.append(("config", f"{CONFIG_FILE} 缺 project_id（缓存副本未落权威值）"))

    try:
        c = client if client is not None else cfg.client_for(cfg.default_persona)
        project = c.get_project_by_key(cfg.project_key)
    except MAPHTTPError as exc:
        if exc.status_code == 404:
            divergences.append(
                ("config", f"project_key '{cfg.project_key}' 未在服务端注册（项目可能已删/改 key）")
            )
            return divergences, EXIT_DIVERGED
        return divergences, _diagnostic(f"权威查询失败 HTTP {exc.status_code}: {exc.detail}")
    except Exception as exc:  # 网络/超时/无 token 等
        return divergences, _diagnostic(str(exc))

    if cfg.project_id is not None and str(cfg.project_id) != str(project.id):
        divergences.append(
            (
                "config",
                f"project_id 陈旧：本地 {cfg.project_id} ≠ 权威 {project.id}"
                "（按键解析，可 `map bootstrap --heal` 回写，不碰 token）",
            )
        )

    try:
        authority_agents = {a.name for a in c.list_agents(project_id=project.id)}
        local_agents = {
            info.agent_name for info in cfg.personas.values() if info.agent_name
        }
    except MAPHTTPError as exc:
        return divergences, _diagnostic(f"权威 agent 查询失败 HTTP {exc.status_code}: {exc.detail}")

    missing = sorted(authority_agents - local_agents)
    extra = sorted(local_agents - authority_agents)
    if missing or extra:
        listed = []
        if missing:
            listed.append(f"本地 agents.yaml 缺权威 agent: {', '.join(missing)}")
        if extra:
            listed.append(f"本地多出未注册 agent: {', '.join(extra)}")
        divergences.append(("agent", "; ".join(listed)))

    return divergences, (EXIT_DIVERGED if divergences else EXIT_CLEAN)


def _diagnostic(reason: str) -> int:
    # 分类专用 exit；reason 仅供诊断输出描述。
    return EXIT_DIAGNOSTIC


def _fix_hint(category: str, msg: str) -> str:
    if "project_id 陈旧" in msg:
        return "map bootstrap --heal（回写权威 project_id，不碰 token）"
    if msg.startswith("project_key"):
        return "map bootstrap --key <key> --name <name>（项目未注册时的正常入口）"
    if category == "agent":
        return "map auth reissue --key <key> --name <agent-name>（恢复/刷新对应 agent）"
    if "缺 project_id" in msg:
        return "map bootstrap --heal（补齐权威 project_id）"
    return "map bootstrap --heal"


@doctor_app.command("config")
def doctor_config(
    check: bool = typer.Option(
        False,
        "--check",
        help="CI 模式：仅按码表退出（0=无分叉 / 1=可修复分叉 / 2=诊断异常），输出一行状态。",
    ),
    project_root: Path | None = typer.Option(None, "--project-root"),
) -> None:
    """对比 .map/ config 与服务端权威，列出分叉项并给出修复命令。

    \b
    判定（A1 码表）：
      0 = 无分叉
      1 = 存在可修复配置分叉（project_id 陈旧 / 缺 project_id / key 未注册 / agent 字段不符）
      2 = 诊断自身异常（权威解析失败、网络、本地标识缺失等，无法判定）
    """
    divergences, code = inspect_config_divergences(project_root=project_root)
    if check:
        if code == EXIT_CLEAN:
            typer.echo("doctor config: clean (0)")
        elif code == EXIT_DIVERGED:
            typer.echo("doctor config: diverged (1)")
            for _, msg in divergences:
                typer.echo(f"  - {msg}", err=True)
        else:
            typer.echo("doctor config: diagnostic-error (2)", err=True)
        raise typer.Exit(code)

    if code == EXIT_CLEAN:
        typer.echo("config 与服务端权威一致，无分叉。")
        return
    if code == EXIT_DIAGNOSTIC:
        typer.echo("无法判定：诊断自身异常（缺少 .map/ 配置或权威查询失败）。", err=True)
        raise typer.Exit(code)
    typer.echo(f"发现 {len(divergences)} 处分叉：")
    for category, msg in divergences:
        typer.echo(f"- [{category}] {msg}")
        typer.echo(f"  修复建议: {_fix_hint(category, msg)}")


def warn_config_divergence(project_root: Path | None = None) -> None:
    """whoami / fs status 的轻量告警钩子：分叉时向 stderr 输出单行提示。

    任何异常（无配置/无网络/非 human 格式判定由调用侧把关）静默跳过，
    不打断正常输出。
    """
    divergences, code = inspect_config_divergences(project_root=project_root)
    if code == EXIT_DIVERGED:
        count = len(divergences)
        hint = _fix_hint(divergences[0][0], divergences[0][1]) if divergences else ""
        typer.echo(
            f"[WARN] config 与服务端权威存在 {count} 处分叉"
            f"（doctor config 复查；首个建议: {hint}）",
            err=True,
        )
