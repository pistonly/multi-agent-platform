"""Local FS plane vs remote projection: diff, delta, auto-sync."""

from __future__ import annotations

import contextlib
import sys
import uuid
from pathlib import Path
from typing import Any

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError
from map_types.schemas.fs import (
    FsExperimentRead,
    FsProjectionChange,
    FsProjectionDeltaRequest,
    FsProjectionPushRequest,
    FsTopicDetailRead,
    fs_experiment_content_hash,
    fs_projection_content_hash,
    fs_topic_content_hash,
)


def _workspace_root(workspace: Path) -> str:
    from cli.commands.fs import _content_root_name

    return _content_root_name(workspace)


def local_projection_objects(workspace: Path) -> tuple[list[FsTopicDetailRead], list[FsExperimentRead]]:
    from map_fs import scan_plane

    from cli.commands.fs import _content_root_name, _experiment_read, fs_topic_to_detail_read

    plane = scan_plane(workspace, _content_root_name(workspace))
    topics = [fs_topic_to_detail_read(t) for t in plane.topics]
    experiments = [_experiment_read(e) for e in plane.experiments]
    return topics, experiments


def describe_sync_state(
    *,
    inventory: Any | None,
    local_hash: str,
    local_content_root: str,
    publisher_ok: bool,
    server_mode: str | None = None,
) -> str:
    if server_mode == "local-fs":
        return "in-sync"
    if inventory is None:
        return "detached"
    blockers: list[str] = []
    if inventory.content_root != local_content_root:
        blockers.append("content_root")
    if not publisher_ok:
        blockers.append("publisher")
    if blockers:
        return "divergent"
    source = getattr(inventory, "source", None)
    if source is not None and source.stale:
        return "stale"
    if inventory.content_hash == local_hash:
        return "in-sync"
    return "local-ahead"


def compute_changes(
    *,
    local_topics: list[FsTopicDetailRead],
    local_experiments: list[FsExperimentRead],
    inventory: Any | None,
    full: bool,
) -> tuple[list[FsProjectionChange], list[dict[str, str]]]:
    local_topic_map = {t.slug: t for t in local_topics}
    local_exp_map = {e.slug: e for e in local_experiments}
    remote: dict[tuple[str, str], str] = {}
    if inventory is not None:
        remote = {(obj.kind, obj.slug): obj.content_hash for obj in inventory.objects}

    changes: list[FsProjectionChange] = []
    summary: list[dict[str, str]] = []

    for slug, topic in sorted(local_topic_map.items()):
        local_hash = fs_topic_content_hash(topic)
        remote_hash = remote.get(("topic", slug))
        if full or remote_hash is None or remote_hash != local_hash:
            changes.append(
                FsProjectionChange(kind="topic_upsert", slug=slug, value=topic)
            )
            summary.append(
                {
                    "kind": "topic",
                    "slug": slug,
                    "action": "add" if remote_hash is None else "modify",
                    "local_hash": local_hash,
                    "server_hash": remote_hash or "",
                }
            )
    for slug, experiment in sorted(local_exp_map.items()):
        local_hash = fs_experiment_content_hash(experiment)
        remote_hash = remote.get(("experiment", slug))
        if full or remote_hash is None or remote_hash != local_hash:
            changes.append(
                FsProjectionChange(kind="experiment_upsert", slug=slug, value=experiment)
            )
            summary.append(
                {
                    "kind": "experiment",
                    "slug": slug,
                    "action": "add" if remote_hash is None else "modify",
                    "local_hash": local_hash,
                    "server_hash": remote_hash or "",
                }
            )
    for (kind, slug), remote_hash in sorted(remote.items()):
        if kind == "topic" and slug not in local_topic_map:
            changes.append(
                FsProjectionChange(
                    kind="topic_delete", slug=slug, expected_hash=remote_hash
                )
            )
            summary.append(
                {
                    "kind": "topic",
                    "slug": slug,
                    "action": "delete",
                    "local_hash": "",
                    "server_hash": remote_hash,
                }
            )
        elif kind == "experiment" and slug not in local_exp_map:
            changes.append(
                FsProjectionChange(
                    kind="experiment_delete", slug=slug, expected_hash=remote_hash
                )
            )
            summary.append(
                {
                    "kind": "experiment",
                    "slug": slug,
                    "action": "delete",
                    "local_hash": "",
                    "server_hash": remote_hash,
                }
            )
    return changes, summary


def build_diff_payload(
    c: MAPClient,
    *,
    pid: uuid.UUID,
    workspace: Path,
) -> dict[str, Any]:
    topics, experiments = local_projection_objects(workspace)
    local_hash = fs_projection_content_hash(topics, experiments)
    local_root = _workspace_root(workspace)
    inventory = c.fs_projection_inventory(pid)
    status = c.fs_plane_status(pid)
    me = c.get_me()
    publisher_ok = True
    if inventory is not None and inventory.publisher_agent_id is not None:
        publisher_ok = (
            me.id == inventory.publisher_agent_id or me.role == "admin"
        )
    blockers: list[str] = []
    if inventory is not None and inventory.content_root != local_root:
        blockers.append(
            f"content_root mismatch: local={local_root} server={inventory.content_root}"
        )
    if inventory is not None and not publisher_ok:
        blockers.append("current persona is not the bound publisher")
    _, summary = compute_changes(
        local_topics=topics,
        local_experiments=experiments,
        inventory=inventory,
        full=False,
    )
    sync_state = describe_sync_state(
        inventory=inventory,
        local_hash=local_hash,
        local_content_root=local_root,
        publisher_ok=publisher_ok,
        server_mode=status.mode,
    )
    next_cmd = {
        "detached": "map fs sync",
        "local-ahead": "map fs sync",
        "divergent": "map fs status  # resolve publisher/content_root blockers",
        "stale": "map fs sync",
        "in-sync": None,
    }.get(sync_state)
    return {
        "sync_state": sync_state,
        "base_revision": None if inventory is None else inventory.projection_revision,
        "local_content_hash": local_hash,
        "server_content_hash": None if inventory is None else inventory.content_hash,
        "publisher_agent_id": None if inventory is None else str(inventory.publisher_agent_id),
        "content_root": local_root,
        "added": [row for row in summary if row["action"] == "add"],
        "modified": [row for row in summary if row["action"] == "modify"],
        "deleted": [row for row in summary if row["action"] == "delete"],
        "blockers": blockers,
        "next": next_cmd,
        "server_mode": status.mode,
        "source": None if status.source is None else status.source.model_dump(mode="json"),
    }


def sync_projection(
    c: MAPClient,
    *,
    pid: uuid.UUID,
    workspace: Path,
    dry_run: bool = False,
    full: bool = False,
    yes: bool = False,
    allow_deletes: bool = True,
) -> dict[str, Any]:
    topics, experiments = local_projection_objects(workspace)
    local_hash = fs_projection_content_hash(topics, experiments)
    local_root = _workspace_root(workspace)
    inventory = c.fs_projection_inventory(pid)
    changes, summary = compute_changes(
        local_topics=topics,
        local_experiments=experiments,
        inventory=inventory,
        full=full or inventory is None,
    )
    deletes = [row for row in summary if row["action"] == "delete"]
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "full": full,
        "changes": summary,
        "local_content_hash": local_hash,
        "base_revision": None if inventory is None else inventory.projection_revision,
    }
    if dry_run:
        result["sync_state"] = "dry-run"
        return result
    if deletes and not allow_deletes:
        result["sync_state"] = "skipped-deletes"
        result["skipped_deletes"] = deletes
        return result
    if deletes and not yes and not sys.stdin.isatty():
        typer.echo(
            "Error: remote objects would be deleted; re-run with --yes "
            "(non-interactive agents must confirm tombstones). Preview: map fs diff",
            err=True,
        )
        raise typer.Exit(1)
    if deletes and not yes and sys.stdin.isatty():
        slugs = ", ".join(f"{row['kind']}:{row['slug']}" for row in deletes)
        confirmed = typer.confirm(f"Delete remote objects ({slugs})?", default=False)
        if not confirmed:
            raise typer.Exit(1)
    if inventory is None or full:
        payload = FsProjectionPushRequest(
            client_workspace=str(workspace),
            content_root=local_root,
            content_hash=local_hash,
            base_revision=None if inventory is None else inventory.projection_revision,
            topics=topics,
            experiments=experiments,
        )
        meta = c.fs_push_projection(pid, payload)
        result.update(meta.model_dump(mode="json"))
        result["bootstrap"] = inventory is None
        result["repair"] = bool(full and inventory is not None)
        return result
    delta = FsProjectionDeltaRequest(
        base_revision=inventory.projection_revision,
        client_workspace=str(workspace),
        content_root=local_root,
        changes=changes,
        result_content_hash=local_hash,
    )
    applied = c.fs_apply_projection_delta(pid, delta)
    result.update(applied.model_dump(mode="json"))
    return result


def maybe_auto_sync(
    *,
    no_sync: bool,
    workspace: Path | None = None,
) -> None:
    """After a local FS write, refresh projection-cache if the server is remote.

    Offline / unreachable server: skip (local file remains the fact).
    Detected remote mode + sync API failure: non-zero exit with a repair hint.
    """
    if no_sync:
        return
    from cli.commands.fs import _workspace
    from cli.main import _cli_options, _resolve_project, resolve_client

    ws = workspace or _workspace()
    try:
        client = resolve_client(
            persona=_cli_options.get("persona"),
            project_root=ws,
        )
        pid = _resolve_project(client, None, None)
        status = client.fs_plane_status(pid)
    except MAPHTTPError as err:
        # 认证/权限/服务端错误不是"离线"：本地写已成功，但投影会静默漂移，
        # 必须可见（不 exit——主写操作已完成，这里只提示修复路径）。
        typer.echo(
            f"Warning: auto-sync skipped due to server error: "
            f"{err.status_code} {err.detail}. Remote projection is now stale; "
            "run `map fs sync` after fixing server access.",
            err=True,
        )
        return
    except Exception:
        # 离线 / 配置缺失：跳过（本地文件仍是事实源）。
        return
    if status.mode == "local-fs":
        return
    try:
        result = sync_projection(
            client,
            pid=pid,
            workspace=ws,
            dry_run=False,
            full=False,
            yes=False,
            allow_deletes=False,
        )
    except MAPHTTPError as err:
        typer.echo(
            f"local write succeeded, remote sync failed: {err.status_code} {err.detail}",
            err=True,
        )
        typer.echo("fix: map fs diff && map fs sync --yes", err=True)
        raise typer.Exit(1) from err
    except Exception as err:
        typer.echo(f"local write succeeded, remote sync failed: {err}", err=True)
        typer.echo("fix: map fs diff && map fs sync --yes", err=True)
        raise typer.Exit(1) from err
    if result.get("sync_state") == "skipped-deletes":
        typer.echo(
            "Warning: auto-sync skipped entirely (including this write) because "
            "remote objects would be deleted. Preview with `map fs diff`, then "
            "`map fs sync --yes`.",
            err=True,
        )
        return
    revision = result.get("projection_revision")
    typer.echo(f"remote sync ok revision={revision}")


def warn_fs_plane_detached(config: Any, *, transport: Any = None) -> None:
    """bootstrap 后的 FS plane 握手：detached 时尝试自动 sync 并给出修复指引。"""
    import uuid
    from pathlib import Path

    if not config.project_id:
        return
    try:
        client = config.client_for(config.default_persona, transport=transport)
    except ValueError:
        return
    try:
        status = client.fs_plane_status(uuid.UUID(str(config.project_id)))
        if status.mode == "local-fs":
            typer.echo(
                f"FS plane: server 可直接读取 workspace（mode=local-fs, "
                f"content_root={status.content_root}）"
            )
            return
        if status.mode in {"detached", "projection-cache"}:
            try:
                workspace = Path(config.map_dir).parent
                result = sync_projection(
                    client,
                    pid=uuid.UUID(str(config.project_id)),
                    workspace=workspace,
                    allow_deletes=False,
                )
                if result.get("sync_state") == "skipped-deletes":
                    typer.echo(
                        "WARNING: bootstrap auto-sync skipped because it would "
                        "delete remote objects. Run `map fs diff` then `map fs sync --yes`.",
                        err=True,
                    )
                else:
                    typer.echo(
                        f"FS plane: auto-synced projection "
                        f"revision={result.get('projection_revision')} "
                        f"(mode was {status.mode})"
                    )
                    return
            except MAPHTTPError as err:
                if err.status_code == 409:
                    # 典型为发布权冲突（projection 绑定在其他 persona/机器），
                    # 不是 workspace 可达性问题——指向正确排查方向。
                    typer.echo(
                        f"WARNING: auto sync after bootstrap rejected (409): "
                        f"{err.detail}",
                        err=True,
                    )
                else:
                    typer.echo(
                        f"WARNING: auto sync after bootstrap failed: "
                        f"{err.status_code} {err.detail}",
                        err=True,
                    )
            except Exception as err:
                typer.echo(f"WARNING: auto sync after bootstrap failed: {err}", err=True)
        typer.echo(
            f"WARNING: FS plane mode={status.mode} —— server 看不到本机 workspace "
            f"({status.workspace_path})。map/ 文件夹话题不会出现在 server 的列表与"
            " work 待办中，验证型写需走 validate → 本地写回 → commit。",
            err=True,
        )
        if status.hint:
            typer.echo(f"  hint: {status.hint}", err=True)
        typer.echo(
            "  修复: 同机运行 server，或在业务仓执行 `map fs sync` "
            "（兼容别名 `map fs push`）。不要把 bind-mount 当作推荐安装路径。",
            err=True,
        )
    except Exception:
        return
    finally:
        with contextlib.suppress(Exception):
            client.close()
