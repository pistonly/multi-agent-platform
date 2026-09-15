"""CLI 参数 / 引用解析（T46：自 ``cli/runner.py`` 拆出）。

本段是纯解析逻辑（``--project`` / ``--creator`` / ``--executor`` / ``--id``
与 resolve payload），不涉及执行链与输出渲染。拆出后由宿主 ``cli/runner.py``
re-export，``runner._resolve_project`` 这类 **模块属性**调用方式与
monkeypatch 面完全不变（T23 约定）。

对宿主的唯一依赖 ``_cli_options``（Typer 全局选项状态，定义在 ``cli.main``）
走函数内 lazy import —— 既避免与宿主底部的 re-export 形成导入环，也保证
测试对 ``cli.main._cli_options`` 的注入仍然生效。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
import yaml
from map_client.client import MAPClient
from map_types.schemas import TopicResolve

from cli.io_helpers import _read_text_file

# ---------------------------------------------------------------------------
# argument / reference resolution
# ---------------------------------------------------------------------------


def _require_option_uuid(value: uuid.UUID | None, *, option: str = "--id") -> uuid.UUID:
    """Typer 0.16 + nested subcommands do not enforce required UUID options."""
    if value is None:
        typer.echo(f"Error: Missing option '{option}'.", err=True)
        raise typer.Exit(2)
    return value


def _require_map_dir(project_root: Path | None = None) -> Path:
    """Require `.map/config.yaml`; exit with bootstrap hint if missing.

    实验 e7244a91（A1）：不再自行 ``find_map_dir``，统一走 ProjectContext
    单点解析；身份 map_dir 取 ``context.config.map_dir``（A3 双根——显式
    ``--config-root`` 时身份来自 config 根）。子命令级 ``--project-root``
    沿用 ``persona whoami`` 既有模式：写回 ``_cli_options`` 后解析。
    """
    from cli import project_context as ctx_mod
    from cli import runner as _runner

    if project_root is not None:
        opts = _runner._cli_options()
        if opts.get("project_root") != project_root:
            opts["project_root"] = Path(project_root)
            ctx_mod.reset_context_cache()
    try:
        context = ctx_mod.current_context()
    except (ctx_mod.ProjectRootNotFoundError, ctx_mod.ConfigRootNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        from map_client.project_config import missing_map_config_message

        typer.echo(f"Error: {missing_map_config_message()} ({exc})", err=True)
        raise typer.Exit(1) from exc
    return context.config.map_dir


def _resolve_project(client: MAPClient, project: uuid.UUID | None, project_key: str | None) -> uuid.UUID:
    from cli.project_context import optional_context

    context = optional_context()
    if context is not None:
        try:
            if project is None and project_key is None:
                return client.get_project_by_key(context.config.project_key).id
        except ValueError:
            pass
    cfg_key = None
    try:
        from map_client.config import load_config

        cfg_key = load_config().get("project_key")
    except (ValueError, OSError, yaml.YAMLError):
        pass
    key = project_key or cfg_key
    return client.resolve_project_id(project, project_key=key)


_PERSONA_SHORT_NAMES: frozenset[str] = frozenset({"host", "participant", "reviewer"})


def _resolve_agent_ref(
    client: MAPClient,
    project_id: uuid.UUID,
    value: str,
    *,
    flag: str,
    label: str,
) -> uuid.UUID:
    """Resolve ``--creator`` / ``--executor`` (UUID, agent_name, or persona short name).

    Resolution order (T26 + plan-mode-direct-execution-productization A):

    1. Valid UUID → pass through (skip ``/agents`` lookup).
    2. Persona short name (``host`` / ``participant`` / ``reviewer``) →
       resolve via ``map_types.persona.pick_agent_by_persona`` against
       ``list_agents(project_id)``, preferring ``{project_key}-{persona}``
       over the trailing-suffix fallback. ``project_key`` comes from
       ``client.get_me().project_key`` so the resolution works in remote
       and isolated projects (no FS read).
    3. Literal agent_name → ``list_agents(project_id)`` exact match,
       ignoring admin rows. 0 hits → error + list available names;
       >1 hits → error (ambiguous).
    """
    try:
        return uuid.UUID(value)
    except ValueError:
        pass
    agents = client.list_agents(project_id=project_id)
    project_key: str | None = None
    if value in _PERSONA_SHORT_NAMES:
        from map_types.persona import pick_agent_by_persona

        try:
            me = client.get_me()
            project_key = getattr(me, "project_key", None)
        except Exception:  # pragma: no cover - get_me is part of the runtime
            project_key = None
        resolved = pick_agent_by_persona(agents, value, project_key=project_key)
        if resolved is not None:
            return resolved.id
    matches = [
        a
        for a in agents
        if a.role.value != "admin" and a.project_id == project_id and a.name == value
    ]
    if len(matches) == 0:
        available = sorted(
            a.name for a in agents if a.role.value != "admin" and a.project_id == project_id
        )
        available_hint = (
            f" Available agent_name in this project: {', '.join(available)}."
            if available
            else " No project-bound agents found in this project."
        )
        typer.echo(
            f"Error: {label} '{value}' not found in current project.{available_hint}",
            err=True,
        )
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.echo(
            f"Error: {label} '{value}' matches {len(matches)} agents in current project; "
            f"name is ambiguous. Pass {flag} <UUID> instead.",
            err=True,
        )
        raise typer.Exit(1)
    return matches[0].id


def _resolve_creator_agent_id(
    client: MAPClient,
    project_id: uuid.UUID,
    creator: str | None,
    creator_agent_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Resolve --creator (name or UUID) and --creator-agent-id into a single creator_agent_id.

    - Neither set → None (no filter).
    - Both set with same value → that value (alias use).
    - Both set with different values → error.
    - --creator is a valid UUID → pass through (skip /agents lookup).
    - --creator is a name → look up via list_agents(project_id); exact match within current
      project, ignoring admin rows. 0 hits → error + list available names;
      >1 hits → error (project-internal name collision).
    """
    if not creator:
        return creator_agent_id
    try:
        creator_uuid = uuid.UUID(creator)
    except ValueError:
        creator_uuid = None
    if creator_uuid is not None:
        if creator_agent_id is not None and creator_agent_id != creator_uuid:
            typer.echo(
                "Error: --creator and --creator-agent-id resolve to different UUIDs.",
                err=True,
            )
            raise typer.Exit(1)
        return creator_uuid
    if creator_agent_id is not None:
        typer.echo(
            "Error: --creator is a name but --creator-agent-id was also passed; "
            "pass one or the other.",
            err=True,
        )
        raise typer.Exit(1)
    return _resolve_agent_ref(
        client, project_id, creator, flag="--creator-agent-id", label="agent_name"
    )


def _resolve_executor_agent_id(
    client: MAPClient,
    project_id: uuid.UUID,
    executor: str,
) -> uuid.UUID:
    """Resolve ``--executor`` (name or UUID) into an ``executor_agent_id``.

    Migration 042: used by ``map experiment start --executor <name|uuid>``
    to delegate execution to another agent. Name lookup shares
    ``_resolve_agent_ref`` with ``--creator`` (T26).
    """
    return _resolve_agent_ref(
        client, project_id, executor, flag="--executor", label="executor"
    )


def _load_topic_resolve_payload(path: Path) -> TopicResolve:
    text = _read_text_file(path, kind="resolve")
    if path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ValueError("resolve YAML must be a mapping")
        return TopicResolve.model_validate(raw)
    return TopicResolve(decision=text)
