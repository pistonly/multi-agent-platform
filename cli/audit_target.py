"""ops-visibility-batch（4770ea76）：audit CLI 双入口的 slug 解析与时间线渲染。

``--target`` 接受话题 slug/uuid5 或实验 slug/uuid/shortid，自动识别
target_type。实验与话题撞名时不静默猜——列出两者报错（实验优先检查）。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError, MAPNotFoundError
from map_types.schemas import AuditLogRead

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.runner import _print_json, _print_yaml  # noqa: E402
from cli.table_render import format_datetime, render_table, short_uuid, truncate

_HEX = re.compile(r"^[0-9a-f]+$")


@dataclass(frozen=True)
class ResolvedTarget:
    target_type: Literal["topic", "experiment"]
    target_id: uuid.UUID
    label: str


def _hex_body(raw: str) -> str:
    return raw.strip().lower().replace("-", "")


def looks_like_uuid_or_shortid(raw: str) -> bool:
    text = _hex_body(raw)
    return bool(8 <= len(text) <= 32 and _HEX.fullmatch(text))


def _optional_workspace() -> Path | None:
    from pathlib import Path

    from map_client.project_config import find_map_dir

    from cli.main import _cli_options  # runtime state (monkeypatch surface)

    start = _cli_options.get("project_root")
    map_dir = find_map_dir(Path(start) if start else None)
    return None if map_dir is None else map_dir.parent


def experiment_slug_from_plan_path(plan_file_path: str | None) -> str | None:
    if not plan_file_path:
        return None
    parts = Path(plan_file_path.replace("\\", "/")).parts
    try:
        idx = parts.index("experiments")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return None


def _local_topic_slug_hit(raw: str) -> str | None:
    from map_fs import parse_topic_dir, topic_id_for_slug

    workspace = _optional_workspace()
    if workspace is None:
        return None
    body = _hex_body(raw)
    if len(body) == 32 and _HEX.fullmatch(body):
        want = uuid.UUID(body)
        topics_dir = workspace / "map" / "topics"
        if not topics_dir.is_dir():
            return None
        for entry in topics_dir.iterdir():
            if entry.is_dir() and topic_id_for_slug(entry.name) == want:
                return entry.name
        return None
    topic_dir = workspace / "map" / "topics" / raw
    t = parse_topic_dir(topic_dir, workspace)
    return t.slug if t is not None else None


def _find_experiment(client: MAPClient, raw: str) -> ResolvedTarget | None:

    project_id = runner._resolve_project(client, None, None)
    body = _hex_body(raw)

    if len(body) == 32 and _HEX.fullmatch(body):
        try:
            exp = client.get_experiment(uuid.UUID(body))
        except MAPNotFoundError:
            exp = None
        if exp is not None:
            return ResolvedTarget("experiment", exp.id, exp.title or str(exp.id)[:8])

    if 8 <= len(body) < 32 and _HEX.fullmatch(body):
        items, _total = client.list_experiments_page(
            project_id, id_prefix=body, page_size=50, include_archived=True
        )
        if len(items) == 1:
            exp = items[0]
            return ResolvedTarget("experiment", exp.id, exp.title or str(exp.id)[:8])
        if len(items) > 1:
            shown = ", ".join(f"{str(e.id)[:8]} ({e.title})" for e in items[:5])
            typer.echo(
                f"Error: experiment id prefix '{raw}' is ambiguous "
                f"({len(items)} matches); lengthen the prefix. Candidates: {shown}",
                err=True,
            )
            raise typer.Exit(2)

    items, _total = client.list_experiments_page(
        project_id, page_size=100, include_archived=True
    )
    slug_hits = [
        e
        for e in items
        if experiment_slug_from_plan_path(e.plan_file_path) == raw
    ]
    if len(slug_hits) == 1:
        exp = slug_hits[0]
        return ResolvedTarget("experiment", exp.id, raw)
    if len(slug_hits) > 1:
        shown = ", ".join(str(e.id)[:8] for e in slug_hits[:5])
        typer.echo(
            f"Error: experiment slug '{raw}' matches {len(slug_hits)} experiments "
            f"({shown}); use a uuid/shortid instead",
            err=True,
        )
        raise typer.Exit(2)
    return None


def _find_topic(client: MAPClient, raw: str) -> ResolvedTarget | None:
    slug = _local_topic_slug_hit(raw)
    if slug is not None:
        from map_fs import topic_id_for_slug

        return ResolvedTarget("topic", topic_id_for_slug(slug), slug)

    body = _hex_body(raw)
    if len(body) == 32 and _HEX.fullmatch(body):
        tid = uuid.UUID(body)
        try:
            topic = client.get_topic(tid)
        except MAPNotFoundError:
            return None
        label = getattr(topic, "slug", None) or getattr(topic, "title", None) or str(tid)[:8]
        return ResolvedTarget("topic", topic.id, str(label))

    # DB slug fallback（存量话题）
    try:

        project_id = runner._resolve_project(client, None, None)
        topics = client.list_topics(project_id, page_size=100)
    except Exception:
        return None
    hits = [t for t in topics if getattr(t, "slug", None) == raw]
    if len(hits) == 1:
        t = hits[0]
        return ResolvedTarget("topic", t.id, raw)
    return None


def resolve_audit_target(client: MAPClient, raw: str) -> ResolvedTarget:
    """解析 ``--target``：实验优先探测，撞名则列出两者报错。"""
    text = (raw or "").strip()
    if not text:
        typer.echo("Error: --target is required and must be non-empty", err=True)
        raise typer.Exit(2)

    experiment = _find_experiment(client, text)
    topic = _find_topic(client, text)
    if experiment is not None and topic is not None:
        typer.echo(
            f"Error: '{text}' matches both an experiment and a topic; "
            "refuse to guess. Specify a uuid/shortid instead.\n"
            f"  experiment: {experiment.target_id} ({experiment.label})\n"
            f"  topic:      {topic.target_id} ({topic.label})",
            err=True,
        )
        raise typer.Exit(2)
    if experiment is not None:
        return experiment
    if topic is not None:
        return topic
    typer.echo(
        f"Error: no topic or experiment matches '{text}'. "
        "Pass a topic slug/uuid5 or an experiment slug/uuid/shortid "
        "(see `map topic list` / `map experiment list`).",
        err=True,
    )
    raise typer.Exit(1)


def fetch_target_audit(
    client: MAPClient,
    target: ResolvedTarget,
    *,
    limit: int = 50,
    kind: str | None = None,
) -> list[AuditLogRead]:
    """GET /audit；FS 话题无 DB 行时 404 → 空列表（不阻断）。"""
    try:
        items = client.list_audit_for_target(
            target.target_type, target.target_id, limit=limit
        )
    except MAPNotFoundError:
        return []
    except MAPHTTPError as exc:
        if exc.status_code == 404:
            return []
        raise
    if kind:
        items = [row for row in items if row.action == kind]
    return items


def fetch_topic_history(
    client: MAPClient,
    topic: ResolvedTarget,
    *,
    limit: int = 50,
    kind: str | None = None,
) -> list[AuditLogRead]:
    """话题 audit + 关联实验 audit，时间倒序，截到 limit（≤200）。"""

    cap = max(1, min(limit, 200))
    chunks: list[list[AuditLogRead]] = [
        fetch_target_audit(client, topic, limit=200, kind=kind)
    ]
    project_id = runner._resolve_project(client, None, None)
    experiments, _total = client.list_experiments_page(
        project_id, page_size=100, include_archived=True
    )
    for exp in experiments:
        if exp.topic_id != topic.target_id:
            continue
        exp_target = ResolvedTarget("experiment", exp.id, exp.title or str(exp.id)[:8])
        chunks.append(fetch_target_audit(client, exp_target, limit=200, kind=kind))
    merged: list[AuditLogRead] = []
    for chunk in chunks:
        merged.extend(chunk)
    merged.sort(key=_audit_sort_ts, reverse=True)
    return merged[:cap]


def _audit_sort_ts(row: AuditLogRead) -> datetime:
    ts = row.created_at
    if ts is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _resolved_format() -> str:
    from cli.main import _cli_options  # runtime state (monkeypatch surface)

    fmt = _cli_options.get("format", "yaml")
    source = _cli_options.get("format_source", "default")
    if source == "default":
        return "table"
    return fmt or "table"


def emit_audit_timeline(
    items: list[AuditLogRead],
    *,
    empty_message: str,
) -> None:
    """C3：table/yaml/json；空结果友好提示，不打空表头。"""

    if not items:
        typer.echo(empty_message)
        return
    fmt = _resolved_format()
    if fmt == "json":
        _print_json([_row_dict(row) for row in items])
        return
    if fmt == "yaml":
        _print_yaml([_row_dict(row) for row in items])
        return
    rows = [
        [
            format_datetime(row.created_at),
            row.action,
            short_uuid(row.agent_id),
            truncate(row.summary, 60),
        ]
        for row in items
    ]
    typer.echo(render_table(["Time", "Action", "Actor", "Summary"], rows))


def _row_dict(row: AuditLogRead) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "action": row.action,
        "actor": str(row.agent_id) if row.agent_id else None,
        "summary": row.summary,
        "target_type": row.target_type,
        "target_id": str(row.target_id) if row.target_id else None,
    }
