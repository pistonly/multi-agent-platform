import contextlib
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml

# T23：``admin_client`` / ``resolve_client`` 的执行体已迁 ``cli.runner``，
# 此处 re-export 是测试注入面（monkeypatch ``cli.main.admin_client`` /
# ``cli.main.resolve_client`` 拦截真实网络调用），勿删。
from map_client.bootstrap import (
    admin_client,  # noqa: F401
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

# re-export(测试/历史引用走 cli.main 路径;实现已随 file/metadata helpers
# 搬至 cli.commands.experiment)
with contextlib.suppress(ImportError):  # map_sdk 可选,不阻塞 CLI 启动
    from map_sdk.evidence import (  # noqa: F401
        EVIDENCE_METADATA_KEYS,
        metadata_has_completion_evidence,
    )

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
from cli.commands.experiment import (  # noqa: F401 — re-export 保持 cli.main._* 兼容引用
    _load_complete_metadata,
    _load_review_verdict_file,
    _print_complete_metadata_schema_and_exit,
    _print_review_verdict_schema_and_exit,
    experiment_app,
)
from cli.commands.feedback import feedback_app
from cli.commands.fs import fs_app
from cli.commands.host import host_app
from cli.commands.notification import inbound_event_app, notification_app
from cli.commands.persona import persona_app
from cli.commands.project import project_app
from cli.commands.runtime import runtime_app
from cli.commands.server import server_app
from cli.commands.skill import skill_app
from cli.commands.sync import sync_app
from cli.commands.topic import mention_app, todo_app, topic_app
from cli.commands.version import version_app
from cli.e2e_collab import e2e_app

# T23（2026-08）：命令执行链（_run / client ctx / envelope / 序列化 /
# 引用解析）已拆至 ``cli.runner``，文件读取辅助拆至 ``cli.io_helpers``。
# 此处 re-export 维持 ``from cli.main import ...`` 的既有导入路径
# （commands 顶层导入已改走 runner / io_helpers；测试与历史调用方仍可
# 从本模块取）。可变状态 ``_cli_options`` / ``_transport`` 仍定义在本
# 模块——runner 经运行时读取，monkeypatch 语义不变。
from cli.io_helpers import _read_text_file, _read_yaml_file  # noqa: F401

# Persona-compare rendering moved to cli.persona_compare (main.py size
# cap, see tests/cli/test_compat.py). Re-exported here so existing
# `from cli.main import ...` call sites keep working.
from cli.persona_compare import (  # noqa: F401
    _PERSONA_COMPARE_DIFF_FIELDS,
    _PERSONA_COMPARE_PARTITION_ALL_AGREE,
    _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD,
    _PERSONA_COMPARE_PARTITION_FULL_DIFF,
    _PERSONA_COMPARE_PARTITION_PARTIAL_DIFF,
    _classify_persona_compare_partition,
    _format_compare_value,
    _persona_compare_view,
    _write_cross_persona_call_audit,
)
from cli.runner import (  # noqa: F401
    CLIErrorEnvelope,
    _admin_client_ctx,
    _client_ctx,
    _emit_deprecation_warnings,
    _emit_evidence_parse_error,
    _emit_json_error_envelope,
    _emit_maphttp_error,
    _emit_similarity_warning,
    _emit_template_warnings,
    _load_topic_resolve_payload,
    _print_json,
    _print_warnings,
    _print_yaml,
    _require_map_dir,
    _require_option_uuid,
    _resolve_admin_api_url,
    _resolve_creator_agent_id,
    _resolve_executor_agent_id,
    _resolve_project,
    _run,
    _to_jsonable,
    _to_yamlable,
    detect_deprecated_aliases,
)
from cli.subcommand_format import make_group_cls
from cli.waker_heartbeat_render import render_waker_heartbeat_banner

# v0.12 M54A: root group class injects a subcommand-level ``--format``
# option into every leaf command (E1). The lambda defers resolution of
# ``_apply_sub_format`` (defined further down, next to the other format
# helpers) — it is only called inside command callbacks at parse time.
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
app.add_typer(fs_app, name="fs")
app.add_typer(docs_app, name="docs")
app.add_typer(e2e_app, name="e2e")
app.add_typer(sync_app, name="sync")
app.add_typer(skill_app, name="skill")
app.add_typer(version_app, name="version")
app.add_typer(host_app, name="host")
app.add_typer(auth_app, name="auth")
app.add_typer(server_app, name="server")

_transport: httpx.BaseTransport | None = None
_cli_options: dict[str, Any] = {"persona": None, "project_root": None, "format": "yaml"}


# 8a8822b5 (a): N=2 release cutoff. ``MAP_CLI_RELEASE_VERSION`` controls
# whether the post-N=2 behavior is active. The build / release script is
# expected to set this env var at install time once N=2 ships; for now
# we default to "0.x" so legacy yaml stays default. The hard-cutover
# behavior is:
#   * if release >= N=2, ``yaml`` / ``legacy`` formats are force-overridden
#     to ``json`` regardless of flag / env, with a one-shot stderr warning;
#   * if ``.map/config.yaml`` declares ``cli.default_format: yaml`` under
#     N=2 release, the same warning fires and the value is ignored.
_N2_RELEASE_MAJOR = 1  # bump here when N=2 ships
_N2_DEFAULT_RELEASE = "0.9"


def _parse_release_version(raw: str | None) -> tuple[int, int]:
    """Parse ``MAJOR.MINOR`` release tuple; return ``(0, 9)`` on any failure.

    The parser is intentionally permissive — anything we cannot interpret
    is treated as pre-N=2 so we never accidentally hard-cutover a script.
    """
    if not raw:
        return (0, 9)
    raw = raw.strip()
    if not raw:
        return (0, 9)
    parts = raw.split(".")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return (0, 9)
    try:
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return (major, 0)
    return (major, minor)


def _is_n2_released() -> bool:
    """True iff ``MAP_CLI_RELEASE_VERSION`` parses to ``>= (_N2_RELEASE_MAJOR, 0)``."""
    raw = os.environ.get("MAP_CLI_RELEASE_VERSION")
    major, minor = _parse_release_version(raw)
    if major > _N2_RELEASE_MAJOR:
        return True
    if major < _N2_RELEASE_MAJOR:
        return False
    return minor >= 0  # any minor in the N=2 major line is in release


def _project_cli_default_format(project_root: Path | None) -> str | None:
    """Read ``cli.default_format`` from ``.map/config.yaml``.

    Returns ``None`` when the project root is not provided, when
    ``.map/config.yaml`` is missing, or when the key is absent. The check
    is intentionally narrow — we only look at the explicit key the release
    checklist flips; everything else (including unknown keys) is ignored.
    """
    if project_root is None:
        return None
    config_path = project_root / ".map" / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    cli_block = data.get("cli")
    if not isinstance(cli_block, dict):
        return None
    value = cli_block.get("default_format")
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def _apply_n2_hard_cutover(
    *,
    current_format: str,
    current_source: str,
    project_root: Path | None,
) -> tuple[str, str, list[str]]:
    """Apply 8a8822b5 (a) post-N=2 yaml hard-cutover.

    Returns ``(new_format, new_source, warnings)``. Warnings are emitted
    by the caller; this function is pure so it is unit-testable without
    touching ``typer.echo``.
    """
    if not _is_n2_released():
        return current_format, current_source, []

    warnings: list[str] = []
    config_default = _project_cli_default_format(project_root)
    if config_default == "yaml":
        warnings.append(
            "Warning: .map/config.yaml `cli.default_format: yaml` is "
            "deprecated in N=2; CLI is forcing json output."
        )
        return "json", "n2-release-cutover", warnings

    # Only warn when the user EXPLICITLY asked for yaml (flag / env / config).
    # The implicit default is the CLI's own choice — N=2 silently swaps it
    # to json without a deprecation warning, since the user never asked for
    # yaml in the first place.
    if current_format == "yaml" and current_source in {
        "explicit --format",
        "MAP_CLI_FORMAT env",
        "config cli.default_format",
    }:
        warnings.append(
            "Warning: yaml output format is removed in N=2; "
            "CLI is forcing json output."
        )
        return "json", "n2-release-cutover", warnings

    # Silent default-yaml → json transition (no warning).
    if current_format == "yaml" and current_source == "default":
        return "json", "n2-release-cutover", []

    return current_format, current_source, warnings


def _apply_sub_format(raw: str | None) -> None:
    """v0.12 M54A: resolve a subcommand-level ``--format`` value.

    Invoked from the leaf-command callback wrapper (cli/subcommand_format.py),
    i.e. AFTER the global callback already resolved the global flag / env
    var — so an explicit subcommand value simply wins. Mirrors the global
    path (legacy alias handling, validation, N=2 hard cutover) so both
    spellings stay equivalent: ``map experiment list --format json`` and
    ``map --format json experiment list``.
    """
    if raw is None:
        return
    resolved = raw.strip().lower()
    if resolved == "legacy":
        typer.echo(
            "Warning: --format legacy is deprecated; "
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
    prev = _cli_options.get("format")
    prev_source = _cli_options.get("format_source", "default")
    if prev is not None and prev != resolved:
        if prev_source == "explicit --format":
            typer.echo(
                f"Warning: subcommand --format={resolved} overrides "
                f"global --format={prev}",
                err=True,
            )
        elif prev_source == "explicit --json":
            typer.echo(
                f"Warning: subcommand --format={resolved} overrides global --json",
                err=True,
            )
        elif prev_source == "MAP_CLI_FORMAT env":
            typer.echo(
                f"Warning: subcommand --format={resolved} overrides "
                f"MAP_CLI_FORMAT={prev}",
                err=True,
            )
    resolved, source, n2_warnings = _apply_n2_hard_cutover(
        current_format=resolved,
        current_source="explicit --format",
        project_root=_cli_options.get("project_root"),
    )
    for warning in n2_warnings:
        typer.echo(warning, err=True)
    _cli_options["format"] = resolved
    _cli_options["format_source"] = f"{source} (subcommand)"


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

_CLEAR_ACTION_TEMPLATES: dict[str, str] = {
    # v0.13 M58: topic write paths are FS-only. The DB-era hints
    # ("--reply-to" thread reply / "advance-round --ack accept") pointed at
    # retired commands; both obligations are now cleared by writing the
    # agent's own round speech file via ``map fs comment``.
    "comment": "map fs comment --topic {topic_id} --file <your-round-speech.md>",
    "ack": "map fs comment --topic {topic_id} --file <your-round-speech.md>  # speech file = your ack (v0.13 M58)",
    "dismiss": "map mention dismiss --id {mention_id}",
    "read": "Read latest comments on topic '{topic_title}'",
}


def render_clear_action_template(work_item: dict[str, Any]) -> str:
    """Render a deterministic CLI hint for a topic work item's clear_action.

    Returns the literal string for the ``clear_action`` value with the
    placeholder fields replaced from the work item payload. The four
    ``clear_action`` values are mapped to the matching ``map`` CLI
    command; ``read`` is a free-form reading instruction (no CLI verb).

    The output is purely mechanical — no LLM, no environment lookups —
    so callers can diff the rendered strings in tests.
    """
    clear_action = work_item.get("clear_action")
    template = _CLEAR_ACTION_TEMPLATES.get(clear_action or "")
    if template is None:
        return f"(unknown clear_action: {clear_action})"
    if clear_action == "read":
        topic_title = work_item.get("topic_title") or "(untitled)"
        return template.format(topic_title=topic_title)
    if clear_action == "dismiss":
        mention_id = (
            work_item.get("mention_id")
            or work_item.get("idempotency_key")
            or work_item.get("source_comment_id")
            or ""
        )
        return template.format(mention_id=mention_id)
    topic_id = work_item.get("topic_id") or ""
    source_comment_id = work_item.get("source_comment_id") or ""
    return template.format(topic_id=topic_id, source_comment_id=source_comment_id)


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
) -> None:
    """Register MAP project + persona agents; write .map/ config (requires admin token)."""
    root = (project_root or Path.cwd()).resolve()
    workspace = path or root
    display_name = name or key

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

    Aggregates multiple read-only API calls into a single human-friendly
    markdown snapshot. Unlike ``status`` (which returns the server's
    status_md narrative) or ``work`` (which returns structured YAML for
    wakers), ``dashboard`` renders a compact, scannable view designed for
    humans who want to see "what's going on" without running several
    commands.

    Data is fetched live on each invocation — no stale snapshots.
    """
    from datetime import datetime

    from map_types import ExperimentPhase, TopicStatus

    try:
        ctx = _client_ctx()
        with ctx as client:
            me = client.get_me()
            project_id = _resolve_project(client, None, None)
            open_topics = client.list_topics(project_id, status=TopicStatus.open)
            experiments = client.list_experiments(project_id)
            todos = client.get_todos()
    except MAPHTTPError as exc:
        _emit_maphttp_error(exc, experiment_id=None, output_format=_cli_options.get("format", "yaml"))
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    persona = _cli_options.get("persona") or "default"
    lines: list[str] = []
    lines.append(f"# MAP Dashboard — {me.name}")
    lines.append(f"_Generated: {now} (persona: {persona})_")
    lines.append("")

    # --- Open Topics ---
    lines.append(f"## Open Topics ({len(open_topics)})")
    if open_topics:
        lines.append("| # | Title | Round | Comments | Experiments | Creator | Created |")
        lines.append("|---|-------|-------|----------|-------------|---------|---------|")
        for i, t in enumerate(open_topics, 1):
            title = (t.title or "").replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            creator = (t.creator_name or "-").replace("|", "\\|")
            created = (t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "-")
            round_label = t.discussion_round.value if hasattr(t.discussion_round, "value") else str(t.discussion_round)
            lines.append(
                f"| {i} | {title} | {round_label} | {t.comment_count} | "
                f"{t.experiment_count} | {creator} | {created} |"
            )
    else:
        lines.append("_(none)_")
    lines.append("")

    # --- Experiments by phase ---
    active_phases = {
        ExperimentPhase.draft,
        ExperimentPhase.review,
        ExperimentPhase.approved,
        ExperimentPhase.running,
        ExperimentPhase.result_review,
    }
    active_exps = [e for e in experiments if e.phase in active_phases]
    done_exps = [e for e in experiments if e.phase == ExperimentPhase.done]
    cancelled_exps = [e for e in experiments if e.phase == ExperimentPhase.cancelled]

    lines.append(f"## Experiments ({len(active_exps)} active / {len(done_exps)} done / {len(cancelled_exps)} cancelled)")
    if active_exps:
        lines.append("| # | Title | Phase | Plan v | Topic | Updated |")
        lines.append("|---|-------|-------|--------|-------|---------|")
        for i, e in enumerate(active_exps, 1):
            title = (e.title or "").replace("|", "\\|")
            if len(title) > 50:
                title = title[:47] + "..."
            updated = (e.updated_at.strftime("%Y-%m-%d %H:%M") if e.updated_at else "-")
            topic = str(e.topic_id)[:8] + "…" if e.topic_id else "-"
            phase_label = e.phase.value if hasattr(e.phase, "value") else str(e.phase)
            lines.append(
                f"| {i} | {title} | {phase_label} | v{e.current_plan_version} | "
                f"{topic} | {updated} |"
            )
    else:
        lines.append("_(no active experiments)_")
    lines.append("")

    # --- Todos summary ---
    todo_buckets = [
        ("pending_reviews", "Pending Reviews", todos.pending_reviews),
        ("pending_result_reviews", "Result Reviews", todos.pending_result_reviews),
        ("pending_topic_replies", "Topic Replies", todos.pending_topic_replies),
        ("pending_round_acks", "Round Acks", todos.pending_round_acks),
        ("pending_advance_rounds", "Advance Rounds", todos.pending_advance_rounds),
        ("stale_open_topics", "Stale Topics", todos.stale_open_topics),
        ("mentions", "Mentions", todos.mentions),
        ("action_items", "Action Items", todos.action_items),
    ]
    obligation_count = sum(len(items) for _, _, items in todo_buckets)
    lines.append(f"## Todos ({obligation_count} obligation items)")
    for _label, display, items in todo_buckets:
        if items:
            lines.append(f"- **{display}**: {len(items)}")
    if obligation_count == 0:
        lines.append("_(no pending obligations)_")
    lines.append("")

    # --- Contextual lists ---
    if todos.my_open_topics:
        lines.append(f"### My Open Topics ({len(todos.my_open_topics)})")
        for t in todos.my_open_topics:
            title = (t.title or "").replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            round_label = t.discussion_round.value if hasattr(t.discussion_round, "value") else str(t.discussion_round)
            lines.append(f"- `{t.id}` — {title} ({round_label}, {t.comment_count} comments)")
        lines.append("")
    if todos.my_open_experiments:
        lines.append(f"### My Open Experiments ({len(todos.my_open_experiments)})")
        for e in todos.my_open_experiments:
            title = (e.title or "").replace("|", "\\|")
            if len(title) > 50:
                title = title[:47] + "..."
            phase_label = e.phase.value if hasattr(e.phase, "value") else str(e.phase)
            lines.append(f"- `{e.id}` — {title} ({phase_label})")
        lines.append("")

    typer.echo("\n".join(lines))


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
