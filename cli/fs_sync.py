"""FS 投影同步与存量迁移 — T45 拆分自 cli/commands/fs.py。

``fs_push`` / ``fs_sync`` / ``fs_diff``（投影上行，走 ``cli.fs_projection``）
与 ``fs_migrate_from_docs``（docs/ → map/ 一次性迁移）。``_workspace`` /
``_content_root_name`` 留守宿主（monkeypatch 面），函数体 call-time 导入。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import typer
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)


def fs_push(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    yes: bool = typer.Option(False, "--yes", help="Confirm remote deletes (tombstones)"),
) -> None:
    """Deprecated alias for ``map sync publish --full``. Prefer ``map sync publish``."""
    typer.echo(
        "Warning: `map sync push` is deprecated; use `map sync publish --full`.",
        err=True,
    )
    _run_fs_sync(project=project, project_key=project_key, dry_run=False, full=True, yes=yes)


def _run_fs_sync(
    *,
    project: uuid.UUID | None,
    project_key: str | None,
    dry_run: bool,
    full: bool,
    yes: bool,
) -> None:
    from cli.commands.fs import _workspace
    from cli.fs_projection import sync_projection

    workspace = _workspace()

    def action(c: MAPClient):
        return sync_projection(
            c,
            pid=runner._resolve_project(c, project, project_key),
            workspace=workspace,
            dry_run=dry_run,
            full=full,
            yes=yes,
        )

    runner._run(action)


def fs_diff(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Compare local map/ with the server projection (summary only, no bodies)."""
    from cli.commands.fs import _workspace
    from cli.fs_projection import build_diff_payload

    workspace = _workspace()

    def action(c: MAPClient):
        return build_diff_payload(
            c, pid=runner._resolve_project(c, project, project_key), workspace=workspace
        )

    def _render(result: dict) -> str:
        lines = [
            f"sync_state : {result['sync_state']}",
            f"revision   : {result['base_revision'] or '(none)'}",
            f"local_hash : {result['local_content_hash'][:12]}…",
            f"server_hash: {(result['server_content_hash'] or '-')[:12]}",
        ]
        for label, key in (("added", "added"), ("modified", "modified"), ("deleted", "deleted")):
            rows = result[key]
            if rows:
                slugs = ", ".join(f"{r['kind']}:{r['slug']}" for r in rows)
                lines.append(f"{label:10}: {slugs}")
        for blocker in result.get("blockers") or []:
            lines.append(f"blocker   : {blocker}")
        if result.get("next"):
            lines.append(f"next      : {result['next']}")
        return "\n".join(lines)

    runner._run(action, table_renderer=_render)


def fs_sync(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    full: bool = typer.Option(False, "--full", help="Upsert every local object, not just the delta"),
    yes: bool = typer.Option(False, "--yes", help="Confirm remote deletes (tombstones)"),
) -> None:
    """Publish local map/ to the server projection with CAS (delta + explicit deletes)."""
    _run_fs_sync(
        project=project,
        project_key=project_key,
        dry_run=dry_run,
        full=full,
        yes=yes,
    )


def _experiment_read(e: Any) -> Any:
    from map_types.schemas.fs import FsExperimentRead

    return FsExperimentRead(
        id=e.id,
        slug=e.slug,
        title=e.title,
        description=e.description,
        phase=e.phase,
        creator=e.creator,
        created_at=e.created_at,
        dir_path=e.dir_path,
        plan_path=e.plan_path,
        log_path=e.log_path,
        review_path=e.review_path,
        current_plan_version=getattr(e, "current_plan_version", 1),
        executor=getattr(e, "executor", ""),
        topic=getattr(e, "topic", ""),
        updated_at=getattr(e, "updated_at", None),
        projection_id=getattr(e, "projection_id", None),
    )


def _load_projection_experiments(
    ctx: Any,
    project: uuid.UUID | None = None,
    project_key: str | None = None,
) -> list[Any]:
    """server 视角实验列表（与 FS 对账用）—— 对账的权威对照侧。

    实验 M2 A2：sync --check 需要 server 视角的实验集合，读
    ``/projects/{pid}/fs/experiments``（local-fs 模式实时解析 workspace，
    远端读 projection cache；端点对无投影项目返回 ``[]``，不 404）。
    ``--project`` / ``--project-key`` 经 ``runner._resolve_project`` 解析，
    未传时回退 context ``project_key``。

    加载失败**向上抛**（实验 M3 A1 收口）：对账门禁不得把「加载失败」
    静默吞成「server 为空」—— 那是 fail-open 假干净（历史上
    ``runner._run`` 无返回值 + config 无 project_id 双缺陷导致本函数
    恒返回 ``[]``，52 个实验全被误判 fs_only）。空列表只可能来自
    端点真实返回，交给五类判定（活跃实验 → divergent blocking）。
    """
    from cli import runner

    with runner._current_client_ctx() as client:
        resolved = runner._resolve_project(client, project, project_key)
        return list(client.list_fs_experiments(resolved))



def _topic_index_meta(topic_dir: Path) -> tuple[str, int, str]:
    """从已迁移的话题文件推导 index.md 元数据：title / round / creator。"""
    from map_fs import parse_round_filename

    max_round = 1
    creator = ""
    for entry in sorted(topic_dir.iterdir()):
        parts = parse_round_filename(entry.name)
        if parts is not None:
            round_number, persona, _is_summary = parts
            max_round = max(max_round, round_number)
            if not creator:
                creator = persona
    return topic_dir.name.replace("-", " ").title(), max_round, creator or "host"


def fs_migrate_from_docs(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """存量迁移：docs/{topics,experiments,map-history} → map/ 下的事实源布局。"""
    import shutil

    from map_fs import write_topic_index

    from cli.commands.fs import _content_root_name, _workspace

    workspace = _workspace()
    root = workspace / _content_root_name(workspace)
    docs = workspace / "docs"
    actions: list[str] = []

    # 1. docs/topics/<slug>/ → map/topics/<slug>/（补生成 index.md）
    src_topics = docs / "topics"
    if src_topics.is_dir():
        for entry in sorted(src_topics.iterdir()):
            if not entry.is_dir():
                continue
            dst = root / "topics" / entry.name
            if dst.exists():
                actions.append(f"skip (exists): {dst.relative_to(workspace)}")
                continue
            title, max_round, creator = _topic_index_meta(entry)
            actions.append(f"move dir: docs/topics/{entry.name} -> {dst.relative_to(workspace)}")
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry), str(dst))
                write_topic_index(
                    workspace,
                    entry.name,
                    title=title,
                    creator=creator,
                    status="open",
                    round_=max_round,
                    content_root=_content_root_name(workspace),
                )

    # 2. docs/experiments/<name>-{plan,log,review}.{md,yaml} → map/experiments/<name>/
    src_exps = docs / "experiments"
    if src_exps.is_dir():
        for entry in sorted(src_exps.iterdir()):
            match = re.match(r"^(.+)-(plan|log|review)\.(md|yaml)$", entry.name)
            if match is None:
                actions.append(f"skip (unknown pattern): docs/experiments/{entry.name}")
                continue
            name = match.group(1)
            dst_dir = root / "experiments" / name
            dst = dst_dir / entry.name.removeprefix(f"{name}-")
            if dst.exists():
                actions.append(f"skip (exists): {dst.relative_to(workspace)}")
                continue
            actions.append(f"move file: docs/experiments/{entry.name} -> {dst.relative_to(workspace)}")
            if not dry_run:
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry), str(dst))
                index = dst_dir / "index.md"
                if not index.exists():
                    index.write_text(
                        f"---\ntitle: {name.replace('-', ' ').title()}\nphase: done\ncreator: host\n---\n",
                        encoding="utf-8",
                    )

    # 3. docs/map-history/ → map/archive/（原样归档，不解析）
    history = docs / "map-history"
    if history.is_dir():
        dst = root / "archive"
        if dst.exists():
            actions.append(f"skip (exists): {dst.relative_to(workspace)}")
        else:
            actions.append(f"move dir: docs/map-history -> {dst.relative_to(workspace)}")
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(history), str(dst))

    if not actions:
        typer.echo("Nothing to migrate (docs/ has no topics/experiments/map-history).")
        return
    prefix = "[dry-run] " if dry_run else ""
    for line in actions:
        typer.echo(prefix + line)
