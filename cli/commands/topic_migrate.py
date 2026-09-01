"""``map topic migrate`` 域命令 — T45 拆分自 topic.py。

Owns ``migrate``（DB 投影 → map/ 文件夹事实源迁移）、``archive-index``、
``migrate-from-docs`` 以及迁移计划/执行 helpers（测试直接 import
``_plan_db_to_fs_migration`` / ``_execute_db_to_fs_migration``）。宿主
``topic_app`` 底部经 :func:`register` 挂载，无循环导入。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import typer
import yaml
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.table_render import enum_value
from cli.topic_routing import _fs_workspace_and_root


def _plan_db_to_fs_migration(topic: Any, workspace: Path) -> dict[str, Any]:
    """DB TopicRead → FS 写入计划（纯函数，便于测试）。

    轮次启发式：round summary 评论界定轮次（summary 归属其所在轮），
    其后的评论进入下一轮；index 轮号不低于 topic.discussion_round。
    同人同轮的多条 DB 评论合并进一个 round<N>-<persona>.md（--- 分隔）。
    """

    def persona_of(agent_name: str | None) -> str:
        if not agent_name:
            return "host"
        cfg = workspace / ".map" / "agents.yaml"
        if cfg.is_file():
            try:
                data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
            personas = data.get("personas") if isinstance(data, dict) else None
            if isinstance(personas, dict):
                for key, meta in personas.items():
                    if isinstance(meta, dict) and meta.get("agent_name") == agent_name:
                        return str(key)
        return agent_name

    def flatten(nodes: Any, out: list[Any]) -> list[Any]:
        for n in nodes or []:
            out.append(n)
            flatten(getattr(n, "children", None), out)
        return out

    groups: dict[tuple[int, str], dict[str, Any]] = {}
    order: list[tuple[int, str]] = []

    def bucket(rn: int, persona: str) -> dict[str, Any]:
        key = (rn, persona)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"bodies": [], "summary": False, "all_system": True}
            order.append(key)
        return g

    round_number = 1
    seen: set[str] = set()
    for cm in sorted(flatten(topic.comments, []), key=lambda x: (x.created_at, x.comment_seq)):
        persona = persona_of(cm.author_name)
        seen.add(persona)
        g = bucket(round_number, persona)
        body = (cm.body or cm.excerpt or "").strip()
        if cm.file_path:
            body = f"*content: {cm.file_path}*\n\n{body}" if body else f"*content: {cm.file_path}*"
        if body:
            g["bodies"].append(body)
        if cm.is_round_summary:
            g["summary"] = True
            round_number += 1
        if str(enum_value(cm.kind)) != "system":
            g["all_system"] = False

    decision = getattr(topic, "decision", None)
    if decision is not None:
        try:
            dump = yaml.safe_dump(decision.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
        except Exception:
            dump = str(decision)
        bucket(round_number, persona_of(getattr(topic, "creator_name", None)))["bodies"].append(
            f"## Decision\n\n```yaml\n{dump}```"
        )

    files: list[tuple[int, str, str, str, bool]] = []
    max_round = 0
    for rn, persona in order:
        g = groups[(rn, persona)]
        files.append(
            (
                rn,
                persona,
                "\n\n---\n\n".join(g["bodies"]) or "*(no content)*",
                "system" if g["all_system"] else "user",
                g["summary"],
            )
        )
        max_round = max(max_round, rn)
    dr = str(enum_value(topic.discussion_round))
    if dr.startswith("round") and dr[5:].isdigit():
        max_round = max(max_round, int(dr[5:]))
    creator_persona = persona_of(getattr(topic, "creator_name", None))
    return {
        "index": {
            "title": topic.title,
            "creator": creator_persona,
            "description": topic.description or "",
            "status": "closed" if str(enum_value(topic.status)) == "closed" else "open",
            "round_": max_round or 1,
            "participants": sorted(seen | {creator_persona}),
        },
        "files": files,
    }


def _execute_db_to_fs_migration(
    c: MAPClient, topic_id: uuid.UUID, slug: str, *, dry_run: bool = False
) -> Any:
    from map_fs import write_round_comment, write_topic_index
    from map_types.schemas import TopicUpdate

    workspace, root = _fs_workspace_and_root()
    target_dir = workspace / root / "topics" / slug
    if target_dir.exists():
        typer.echo(f"Error: target already exists: {target_dir} (pick another --slug)", err=True)
        raise typer.Exit(1)

    topic = c.get_topic(topic_id)
    plan = _plan_db_to_fs_migration(topic, workspace)
    if dry_run:
        idx = plan["index"]
        typer.echo(
            f"[dry-run] write {target_dir / 'index.md'} "
            f"(status={idx['status']}, round={idx['round_']}, participants={','.join(idx['participants'])})"
        )
        for rn, persona, _body, _kind, summary in plan["files"]:
            suffix = " (round summary)" if summary else ""
            typer.echo(f"[dry-run] write {target_dir / f'round{rn}-{persona}.md'}{suffix}")
        typer.echo(f"[dry-run] archive DB topic {topic_id} (archived=true)")
        return None
    index_path = write_topic_index(workspace, slug, content_root=root, **plan["index"])
    typer.echo(f"Wrote {index_path}")
    for rn, persona, body, kind, summary in plan["files"]:
        path = write_round_comment(
            workspace,
            slug,
            round_number=rn,
            persona=persona,
            body=body,
            kind=kind,
            is_round_summary=summary,
            content_root=root,
        )
        typer.echo(f"Wrote {path}")
    updated = c.update_topic(topic_id, TopicUpdate(archived=True))
    typer.echo(f"Archived DB topic {topic_id} (hidden from list; show still works)")
    return updated


# ---------------------------------------------------------------------------
# mention_app
# ---------------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Register migrate-domain commands on the host ``topic_app``."""
    @app.command("migrate")
    def topic_migrate(
        topic_id: uuid.UUID = typer.Option(..., "--id", help="DB topic UUID to migrate."),
        slug: str = typer.Option(..., "--slug", help="Target folder name: map/topics/<slug>/"),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="List planned writes without touching files or the DB."
        ),
    ) -> None:
        """Migrate a DB topic to the map/ folder source of truth (one-way, M51).

        FS 完整落盘（index.md + 全部 round 文件）成功后才 archive DB 记录
        （列表默认隐藏，show 仍可见）；中途失败不产生半迁移。
        """

        runner._run(lambda c: _execute_db_to_fs_migration(c, topic_id, slug, dry_run=dry_run))


    @app.command("archive-index")
    def topic_archive_index(
        rebuild: bool = typer.Option(
            False, "--rebuild", help="全量重建（生成式投影，唯一模式）"
        ),
    ) -> None:
        """重建 map/archive/INDEX.md。"""
        from cli.commands.fs import fs_archive_index

        fs_archive_index(rebuild=rebuild)


    @app.command("migrate-from-docs")
    def topic_migrate_from_docs(
        dry_run: bool = typer.Option(False, "--dry-run"),
    ) -> None:
        """存量迁移：docs/{topics,experiments,map-history} → map/ 下的事实源布局。"""
        from cli.commands.fs import fs_migrate_from_docs

        fs_migrate_from_docs(dry_run=dry_run)
