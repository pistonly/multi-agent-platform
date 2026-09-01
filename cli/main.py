import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import typer

# T23：``admin_client`` / ``resolve_client`` 的执行体已迁 ``cli.runner``，
# 此处 re-export 是测试注入面（monkeypatch ``cli.main.admin_client`` /
# ``cli.main.resolve_client`` 拦截真实网络调用），勿删。
from map_client.bootstrap import (
    DEFAULT_PERSONAS,
    admin_client,  # noqa: F401
    bootstrap_local_map,
    bootstrap_project_map,
    heal_project_map_config,
)
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError
from map_client.project_config import (
    find_map_dir,
    load_project_map_config,
    resolve_client,  # noqa: F401
)

# arch experiment (0519e2a3) PR1: shared SDK umbrella. Later PRs move
# shared helpers here (see plan). The CLI must stay importable even
# when ``map_sdk`` is unavailable, so we don't gate startup on it.
from map_types.enums import AgentRole

# arch experiment (0519e2a3) PR3/PR5: agent / notification /
# inbound-event / audit sub-app splits. Each module only exports the
# sub-app instance here; the helpers the commands use come from
# ``cli.runner`` / ``cli.io_helpers`` (T23) via the re-export below —
# commands import those at module top level, no more lazy imports.
from cli.commands.action import action_app
from cli.commands.agent import agent_app
from cli.commands.audit import audit_app
from cli.commands.auth import auth_app
from cli.commands.docs import docs_app
from cli.commands.doctor import doctor_app
from cli.commands.experiment import experiment_app
from cli.commands.feedback import feedback_app
from cli.commands.host import host_app
from cli.commands.mention import mention_app  # T45: split from topic.py
from cli.commands.notification import inbound_event_app, notification_app
from cli.commands.persona import persona_app
from cli.commands.project import project_app
from cli.commands.runtime import runtime_app
from cli.commands.server import server_app
from cli.commands.skill import skill_app
from cli.commands.sync import sync_app
from cli.commands.todo import todo_app  # T45: split from topic.py
from cli.commands.topic import topic_app
from cli.commands.verify_audit import verify_audit_app
from cli.commands.version import version_app
from cli.commands.waker_status import waker_app
from cli.e2e_collab import e2e_app

# T23（2026-08）：命令执行链（_run / client ctx / envelope / 序列化 /
# 引用解析）已拆至 ``cli.runner``，文件读取辅助拆至 ``cli.io_helpers``。
# 此处导入的是本模块自用的执行链入口；``_client_ctx`` / ``_admin_client_ctx``
# 同时是注入面——runner 经运行时 ``_main._client_ctx()`` 解析，测试 setattr
# 替换后仍生效。其余历史 re-export 已随 T43 死代码清理移除（调用方
# 一律直接导入 cli.runner / cli.io_helpers / cli.persona_compare）。
# 可变状态 ``_cli_options`` / ``_transport`` 仍定义在本模块——runner
# 经运行时读取，monkeypatch 语义不变。
from cli.runner import (  # noqa: F401 — _client_ctx/_admin_client_ctx 为注入面
    _admin_client_ctx,
    _client_ctx,
    _emit_maphttp_error,
    _resolve_project,
    _run,
)

# v0.12 M54A: root group class injects a subcommand-level ``--format``
# option into every leaf command (E1). The lambda defers resolution of
# ``_apply_sub_format`` (T33: defined in ``cli/subcommand_format.py``,
# next to the leaf-patch machinery) — it is only called inside command
# callbacks at parse time. The N=2 hard-cutover helpers co-live there;
# the ones used by the global callback below are imported explicitly.
from cli.subcommand_format import (
    _apply_n2_hard_cutover,
    _apply_sub_format,
    _is_n2_released,
    _project_cli_default_format,
    make_group_cls,
)
from cli.waker_heartbeat_render import render_waker_heartbeat_banner

app = typer.Typer(
    name="map",
    help="Multi-Agent Platform CLI",
    rich_markup_mode=None,
    cls=make_group_cls(lambda raw: _apply_sub_format(raw)),
)
# Sub-apps live in cli/commands/*; imported here only to register via
# add_typer. Command bodies lazy-import helpers from this module to
# break ``cli.main ↔ cli.commands.*`` cycles.
app.add_typer(project_app, name="project")
app.add_typer(experiment_app, name="experiment")
app.add_typer(persona_app, name="persona")
app.add_typer(runtime_app, name="runtime")
app.add_typer(agent_app, name="agent")
app.add_typer(notification_app, name="notification")
app.add_typer(inbound_event_app, name="inbound-event")
app.add_typer(audit_app, name="audit")
app.add_typer(topic_app, name="topic")
app.add_typer(mention_app, name="mention")
app.add_typer(todo_app, name="todo")
app.add_typer(action_app, name="action")
app.add_typer(feedback_app, name="feedback")
app.add_typer(doctor_app, name="doctor")
app.add_typer(docs_app, name="docs")
app.add_typer(e2e_app, name="e2e")
app.add_typer(sync_app, name="sync")
app.add_typer(skill_app, name="skill")
app.add_typer(version_app, name="version")
app.add_typer(host_app, name="host")
app.add_typer(auth_app, name="auth")
app.add_typer(server_app, name="server")
app.add_typer(waker_app, name="waker")
# 实验 e6d23886 I3：verify-audit CLI 子命令（map fs verify-audit）。
app.add_typer(verify_audit_app, name="fs")

_transport: httpx.BaseTransport | None = None
_cli_options: dict[str, Any] = {"persona": None, "project_root": None, "format": "yaml"}


def _cli_version() -> str:
    """Best-effort CLI version string.

    Order: ``map_sdk.__version__`` first — it is the in-tree source of
    truth, pinned to ``pyproject [project] version`` by
    ``tests/test_eng_version_single_source.py``, and stays correct for
    editable installs after a version bump (installed metadata goes
    stale until the next ``pip install -e .`` / ``uv sync``). Package
    metadata is the fallback when map_sdk is somehow unavailable.
    """
    try:
        import map_sdk

        return map_sdk.__version__
    except Exception:
        pass
    try:
        from importlib.metadata import version as _dist_version

        return _dist_version("multi-agent-platform")
    except Exception:
        return "unknown"


def _version_callback(value: bool) -> None:
    """Eager ``--version`` handler: print and exit before any subcommand."""
    if value:
        typer.echo(f"map {_cli_version()}")
        raise typer.Exit()


@app.callback()
def cli_global_options(
    version_flag: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the map CLI version and exit.",
    ),
    persona: str | None = typer.Option(
        None,
        "--persona",
        "-p",
        help="Persona from .map/agents.local.yaml (host, participant, reviewer, …)",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Code repo root containing .map/ (default: search upward from cwd)",
    ),
    output_format: str | None = typer.Option(
        None,
        "--format",
        "-o",
        help=(
            "Output format: 'table' (list commands default) renders a compact "
            "scannable table; 'yaml' (default for non-list commands) dumps the "
            "full structured object; 'json' emits machine-parseable JSON to "
            "stdout with a structured error envelope on stderr. 'legacy' is an "
            "alias for 'yaml' and will be removed in N=2. "
            "Explicit --format always wins over the MAP_CLI_FORMAT env var."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Shortcut for --format json. Overrides --format and MAP_CLI_FORMAT.",
    ),
) -> None:
    # 8a8822b5 (f): resolve --format / MAP_CLI_FORMAT priority.
    # --json shortcut > Explicit --format flag > MAP_CLI_FORMAT env var > default 'yaml'.
    env_format = os.environ.get("MAP_CLI_FORMAT", "").strip().lower() or None
    resolved: str | None
    source: str
    if json_output:
        resolved = "json"
        source = "explicit --json"
    elif output_format is not None:
        resolved = output_format.lower()
        source = "explicit --format"
        if env_format and env_format != resolved:
            typer.echo(
                f"Warning: explicit --format={resolved} overrides "
                f"MAP_CLI_FORMAT={env_format}",
                err=True,
            )
    elif env_format:
        resolved = env_format
        source = "MAP_CLI_FORMAT env"
    else:
        resolved = "yaml"
        source = "default"

    # 8a8822b5 (a): post-N=2 — ``.map/config.yaml cli.default_format`` becomes
    # an explicit project-level source (sits between env var and the implicit
    # default). Pre-N=2 keeps the legacy default to avoid breaking existing
    # scripts that have not migrated.
    if (
        _is_n2_released()
        and output_format is None
        and env_format is None
    ):
        config_default = _project_cli_default_format(project_root)
        if config_default in ("yaml", "json"):
            resolved = config_default
            source = "config cli.default_format"

    if resolved == "legacy":
        typer.echo(
            "Warning: MAP_CLI_FORMAT=legacy is deprecated; "
            "use 'yaml' explicitly. The 'legacy' alias will be removed in N=2.",
            err=True,
        )
        resolved = "yaml"

    if resolved not in ("yaml", "json", "table"):
        typer.echo(
            f"Error: unknown --format {resolved!r}; expected 'table', 'yaml', 'json', or 'legacy'.",
            err=True,
        )
        raise typer.Exit(2)

    # 8a8822b5 (a): post-N=2 yaml hard-cutover.
    resolved, source, n2_warnings = _apply_n2_hard_cutover(
        current_format=resolved,
        current_source=source,
        project_root=project_root,
    )
    for warning in n2_warnings:
        typer.echo(warning, err=True)

    _cli_options["persona"] = persona
    _cli_options["project_root"] = project_root
    _cli_options["format"] = resolved
    _cli_options["format_source"] = source


def _warn_fs_plane_detached(config: Any, *, transport: Any = None) -> None:
    from cli.fs_projection import warn_fs_plane_detached

    warn_fs_plane_detached(config, transport=transport)


@app.command("bootstrap")
def map_bootstrap(
    key: str = typer.Option(..., "--key", help="MAP project_key for this code repo"),
    name: str | None = typer.Option(None, "--name", help="Human-readable MAP project name"),
    path: Path | None = typer.Option(None, "--path", help="workspace_path stored on MAP project"),
    description: str | None = typer.Option(None, "--description"),
    api_url: str | None = typer.Option(None, "--api-url", help="MAP API base URL"),
    project_root: Path | None = typer.Option(None, "--project-root", help="Where to write .map/"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing .map/agents.local.yaml"),
    heal: bool = typer.Option(
        False,
        "--heal",
        help="非破坏性修复（3b7c2b44 A2）：key 已存在时跳过 create，把 config.yaml 的 "
        "project_id 与 agents.yaml 的 agent_name 回写服务端权威，不碰 token。",
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="离线本地平面（plane: local）：零注册/零 token/零网络，只写 .map/ 配置与内容根，"
        "不调 server。适合 agent workspace 内的一次性 run 级项目。",
    ),
    personas: str = typer.Option(
        "host,participant,reviewer",
        "--personas",
        help="逗号分隔的 persona 列表（仅 --local 生效；默认 host,participant,reviewer）。",
    ),
) -> None:
    """Register MAP project + persona agents; write .map/ config (requires admin token)."""
    root = (project_root or Path.cwd()).resolve()
    workspace = path or root
    display_name = name or key

    if local:
        persona_defs: dict[str, dict[str, str]] = {}
        for raw in (part.strip() for part in personas.split(",")):
            if not raw:
                continue
            spec = DEFAULT_PERSONAS.get(raw, {})
            persona_defs[raw] = {
                "agent_name_suffix": spec.get("agent_name_suffix") or raw,
                "description": spec.get("description"),
                "role": spec.get("role") or "agent",
            }
        try:
            result = bootstrap_local_map(
                project_key=key,
                project_name=display_name,
                project_root=root,
                api_url=api_url,
                personas=persona_defs or None,
            )
            from cli.commands.fs import fs_init

            fs_init(root)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Wrote {result.config.map_dir}/ (plane: local — 离线项目，不注册平台)")
        typer.echo(f"MAP project_key={result.config.project_key} id={result.config.project_id}")
        typer.echo("Personas: " + ", ".join(sorted(result.config.personas)))
        typer.echo("Try: map --persona host topic create --title <title>")
        return

    if heal:
        try:
            healed = heal_project_map_config(
                project_key=key,
                project_root=root,
                api_url=api_url,
                transport=_transport,
            )
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        except MAPHTTPError as exc:
            typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"heal: project_key={healed.project_key} project_id={healed.project_id}")
        typer.echo(
            f"  config.yaml project_id: "
            f"{'已回写权威值' if healed.config_rewritten else '与权威一致，无需改动'}"
        )
        fix_total = len(healed.fixed_agent_names or [])
        typer.echo(
            f"  agents.yaml agent_name: "
            f"{'已回写 ' + str(fix_total) + ' 个约定候选' if healed.agents_rewritten else '无需改动'}"
        )
        for persona_key, old, new in healed.fixed_agent_names or []:
            typer.echo(f"    - {persona_key}: {old} -> {new}")
        typer.echo("  agents.local.yaml token: 未触碰（该文件 byte 保持不变）")
        typer.echo("Try: map doctor config --check 复查对账")
        return

    try:
        result = bootstrap_project_map(
            project_key=key,
            project_name=display_name,
            workspace_path=workspace,
            project_root=root,
            api_url=api_url,
            description=description,
            force=force,
            transport=_transport,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except MAPHTTPError as exc:
        if exc.status_code == 409:
            # A3：自服务路径 server 已存在 key 的 409 → 按意图分流
            from map_client.bootstrap import bootstrap_conflict_triage

            typer.echo(f"Error 409: {exc.detail}", err=True)
            typer.echo(bootstrap_conflict_triage(key), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Wrote {result.config.map_dir}/")
    typer.echo(f"MAP project_key={result.config.project_key} id={result.config.project_id}")
    if result.created_project:
        typer.echo("Created new MAP project.")
    else:
        typer.echo("Reused existing MAP project.")
    _warn_fs_plane_detached(result.config, transport=_transport)
    if result.skipped_agent_names:
        typer.echo(
            "Skipped existing agents (tokens not recoverable via bootstrap): "
            + ", ".join(result.skipped_agent_names),
            err=True,
        )
        typer.echo(
            "Recover them with `map auth reissue --key "
            f"{result.config.project_key} --name <agent-name>`, or keep your "
            "existing .map/agents.local.yaml."
        )
    typer.echo("Personas: " + ", ".join(result.config.tokens.keys()))
    typer.echo(
        "Backup tip: tokens are shown once — copy .map/agents.local.yaml to a "
        "safe place. If lost, recover with `map auth reissue --key "
        f"{result.config.project_key} --name <agent-name>`."
    )
    typer.echo("Try: map --persona host status")


@app.command("me")
def map_me() -> None:
    """Alias for `map persona whoami`."""

    def action(c: MAPClient):
        me = c.get_me()
        payload = me.model_dump(mode="json")
        if _cli_options.get("persona"):
            payload["persona"] = _cli_options["persona"]
        elif find_map_dir(_cli_options.get("project_root")):
            payload["persona"] = load_project_map_config(
                project_root=_cli_options.get("project_root")
            ).default_persona
        return payload

    _run(action)


@app.command("todos")
def map_todos() -> None:
    _run(lambda c: c.get_todos())


def _print_work_kinds(explain: str | None, fmt: str = "list") -> None:
    """``map work --kinds`` / ``--explain <kind>``：输出 server 侧 KINDS registry。

    实验 d559f431（work-kind-dispatch-single-source）A2 方向 A 过渡：wake.md
    分发表以此输出为静态引用基准，CI 逐行一致校验（A3）消费同一 registry。
    ``fmt="md"`` 输出 ``render_kinds_md()`` 渲染形态（与 wake.md 标记块
    逐字符一致的对照基准）。
    """
    # 延迟 import：避免 CLI 启动路径拖入 server 依赖（先例 simple_waker 的 server_config）
    from server.services.work_kinds import (
        WORK_ITEM_KINDS,
        get_kind_spec,
        render_kinds_md,
    )

    if fmt == "md" and explain is None:
        typer.echo(render_kinds_md())
        return


    if explain is not None:
        spec = get_kind_spec(explain)
        if spec is None:
            typer.echo(
                f"未登记的 kind: {explain}（registry 漂移信号，见 server/services/work_kinds.py）",
                err=True,
            )
            raise typer.Exit(code=2)
        specs = (spec,)
    else:
        specs = WORK_ITEM_KINDS

    for spec in specs:
        typer.echo(f"kind: {spec.kind}")
        typer.echo(f"  clear_action: {spec.clear_action}")
        typer.echo(f"  skill: {spec.skill}")
        if spec.note:
            typer.echo(f"  note: {spec.note}")
        typer.echo()


@app.command("work")
def map_work(
    kinds: bool = typer.Option(
        False,
        "--kinds",
        help="输出 work item kind 分发 registry（清理动作+归属 Skill+说明；server 侧单一真相，实验 d559f431 方向 A）",
    ),
    explain: str | None = typer.Option(
        None,
        "--explain",
        help="输出单个 kind 的分发规格（如 --explain mentions；未登记 kind 退出码 2）",
    ),
    kinds_format: str = typer.Option(
        "list",
        "--kinds-format",
        help="kinds 输出形态：list（逐条人类可读）或 md（wake.md 标记块对照基准，实验 d559f431 A3）",
    ),
    notification_limit: int = typer.Option(50, "--notification-limit", min=1, max=200),
    notification_category: str = typer.Option(
        "all",
        "--notification-category",
        help="all (human default), wakeable (waker view), or digest",
    ),
    client: str | None = typer.Option(None, "--client", help="'waker' 标记为 simple-waker 轮询（一并刷 last_waker_poll_at）"),
    summary: bool = typer.Option(
        False,
        "--summary",
        help="Return the compact 6-bucket by_kind summary instead of the full work snapshot.",
    ),
    include_all_personas: bool = typer.Option(
        False,
        "--include-all-personas",
        help="Include host-only buckets (explicit_only, informational_only, action_items) when the current persona would otherwise hide them.",
    ),
    topics_limit: int = typer.Option(
        10,
        "--summary-topics-limit",
        min=1,
        max=100,
        help="Max topics per summary bucket (default 10).",
    ),
    experiments_limit: int = typer.Option(
        5,
        "--summary-experiments-limit",
        min=1,
        max=50,
        help="Max experiments per summary bucket (default 5).",
    ),
) -> None:
    """Unified work snapshot: whoami + topic-progress + todos + unread notifications.

    CLI defaults to ``all`` so humans see the same unread count as
    ``notification list --unread-only``. Wakers should pass
    ``--notification-category wakeable`` explicitly.

    Pass ``--summary`` to get the compact 6-bucket by_kind summary for the
    /work top card; combine with ``--include-all-personas`` for full
    visibility.
    """
    if kinds or explain is not None:
        _print_work_kinds(explain, fmt=kinds_format)
        return

    if summary:

        def action(c: MAPClient):
            return c.get_agent_work_summary(
                include_all_personas=include_all_personas,
                topics_limit=topics_limit,
                experiments_limit=experiments_limit,
            )

        _run(action, detect_deprecated=True)
        return

    def action(c: MAPClient):
        render_waker_heartbeat_banner(c)
        return c.get_agent_work(
            notification_limit=notification_limit,
            notification_category=notification_category,
            client=client,
        )

    _run(action, detect_deprecated=True)


@app.command("status")
def project_or_global_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    def action(c: MAPClient):
        if project is not None or project_key is not None:
            pid = _resolve_project(c, project, project_key)
            return c.get_project_status(pid)
        me = c.get_me()
        if me.role == AgentRole.admin:
            return c.get_global_status()
        pid = _resolve_project(c, None, None)
        return c.get_project_status(pid)

    _run(action)


@app.command("dashboard")
def map_dashboard() -> None:
    """One-glance markdown overview: identity, open topics, experiments, todos.

    T33: rendering lives in ``cli.dashboard`` (lazy import keeps the CLI
    startup path free of the renderer; no import cycle since that module
    reads runtime state through this module object).
    """
    from cli.dashboard import render_dashboard

    render_dashboard()


def _verify_runtime_imports() -> None:
    """Fail fast when a bundled SDK package is missing from the environment.

    ``pip install -e .`` freezes the editable package map at install time;
    a top-level package added later (``map_fs``, v0.10) stays invisible to
    a stale editable finder. Every ``map_fs`` import in the command layer
    is a lazy in-function import, so the drift otherwise only surfaces as a
    raw ``ModuleNotFoundError`` traceback deep inside an FS-scanning
    command (observed: ``map topic list`` crashing long after the package
    landed). ``map_client`` / ``map_sdk`` / ``map_types`` are already
    imported at module scope above; ``map_fs`` is the only gap this probe
    covers.
    """
    try:
        import map_fs  # noqa: F401
    except ImportError:
        typer.echo(
            "Error: SDK package 'map_fs' is missing from this Python environment.\n"
            "This usually means the editable install is stale (created before\n"
            "map_fs was added to the distribution).\n"
            "Recover: pip install -e . --force-reinstall --no-deps",
            err=True,
        )
        raise typer.Exit(1) from None


def main() -> None:
    _verify_runtime_imports()
    app()


if __name__ == "__main__":
    # ``python -m cli.main`` runs this file as ``__main__``; importing main
    # from the canonical ``cli.main`` module keeps a single module instance so
    # the global callback and command bodies share the same ``_cli_options``
    # (otherwise ``--persona`` written to the ``__main__`` copy is lost and
    # resolution falls back to default_persona).
    from cli.main import main as _main

    _main()
