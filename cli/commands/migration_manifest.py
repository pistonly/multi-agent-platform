"""``map sync migrate ...`` sub-app —— DB → FS projection 存量迁移（实验 M2 I5+I6 → M3 A2 收口）。

子命令与 server 端 ``migration_manifest_service`` / ``/fs-migration`` API 一一对应：

- ``scan`` —— 枚举 DB experiments + topics，建 ``migration_manifest_items`` 行；
  重跑幂等（idempotency_key UNIQUE）。
- ``dry-run`` —— **只读** plan：actionable 项 + stale in_flight 计数 + 汇总；
  零写副作用（host worker ``--dry-run`` 模式会真实执行本命令）。
- ``execute`` —— server 端逐 item claim / apply / mark，逐 item 提交事务
  （断点真实存在，中断后重跑自动续）；``--apply`` 走 fs_projection delta 真
  CAS，缺省（dry）记 ``skipped`` 排练标记，不再伪造 ``applied`` 终态。
- ``verify`` —— server 端把 applied 项与 server FS 视角按 A2 字段契约逐项
  比对；退出码 0=全部 verified，2=有 mismatch/missing。
- ``status`` —— project 级状态汇总 + 最近 run。

M3 A2 变更：CLI **不再 import ``server.db``**——全部经 SDK client 走 HTTP
API（权限/审计收进 server；非 server 主机上不再静默连错库）。LKG 锚点保留
为 client-side 记录：``<content_root>/.fs-migration/<project_key>-lkg.json``，
project 作用域命名 + 原子写 + 读时校验 project_id（M3 A2 修 L3）。
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)

migration_app = typer.Typer(
    help=(
        "DB → FS projection 存量迁移 manifest（实验 M2 I5+I6 / M3 A2）。子命令："
        "scan / dry-run / execute / verify / status。"
    ),
    no_args_is_help=True,
)


def _emit_json(obj: object) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def _lkg_path(workspace_root: str | Path, content_root: str, project_key: str) -> Path:
    return (
        Path(workspace_root)
        / content_root
        / ".fs-migration"
        / f"{project_key or 'default'}-lkg.json"
    )


def _write_lkg_atomic(lkg_path: Path, payload: dict) -> None:
    """原子写 LKG（tmp + os.replace）：中断不留半截 JSON。"""
    lkg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = lkg_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, lkg_path)


@migration_app.command("scan")
def migrate_scan(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Scan 阶段：枚举 DB experiments + topics，建 manifest item 行（幂等）。"""

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        report = c.fs_migration_scan(pid)
        if as_json:
            _emit_json(report)
            return
        typer.echo(f"Scan run_id={report['run_id']}")
        typer.echo(
            f"  scanned={report['scanned']} inserted={report['inserted']} "
            f"skipped_existing={report['skipped_existing']}"
        )
        typer.echo(f"  by_kind={report['by_kind']}")
        typer.echo(f"  by_status={report['by_status']}")

    runner._run(action)


@migration_app.command("dry-run")
def migrate_dry_run(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    limit: int = typer.Option(50, "--limit", help="最多展示的 actionable item 数"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Dry-run（只读）：actionable 项 + stale 计数 + 汇总，零写副作用。"""

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        plan = c.fs_migration_plan(pid, limit=limit)
        if as_json:
            _emit_json(plan)
            return
        typer.echo(
            f"Dry-run actionable={plan['actionable_count']} "
            f"stale_in_flight={plan['stale_in_flight']}"
        )
        typer.echo(f"  summary={plan['summary']}")
        for it in plan["actionable"]:
            typer.echo(
                f"  - [{it['kind']}/{it['status']}] {it['slug']}  "
                f"hash={str(it['content_hash'])[:12]}…  attempts={it['attempts']}"
            )

    runner._run(action)


@migration_app.command("execute")
def migrate_execute(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    limit: int = typer.Option(50, "--limit", help="本轮最多执行的 item 数"),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="真 CAS apply（写 server 投影）；缺省为 dry 排练（记 skipped，不写 server）",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Execute 阶段：server 端逐 item claim / apply / mark，逐 item 提交。

    ``--apply`` 缺省只做排练（skipped）；真 apply 需要显式传参。中断后
    重跑自动从断点续（applied 不重复、pending 续跑）。
    """

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        report = c.fs_migration_execute(pid, apply=apply, limit=limit)
        if as_json:
            _emit_json(report)
            return
        typer.echo(f"Execute run_id={report['run_id']} apply={report['apply']}")
        typer.echo(
            f"  stale_in_flight_reset={report['stale_in_flight_reset']} "
            f"processed={report['processed']} failed={report['failed']} "
            f"unclaimed={report['unclaimed']}"
        )
        typer.echo(f"  summary={report['summary']}")

    runner._run(action)


@migration_app.command("verify")
def migrate_verify(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Verify 阶段：applied 项 vs server FS 视角逐字段比对；写 LKG 锚点。

    退出码 0=全部 verified；2=存在 mismatch / missing（blocking）。
    """
    from cli.project_context import ProjectRootNotFoundError, current_context

    try:
        ctx = current_context()
    except ProjectRootNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        report = c.fs_migration_verify(pid)
        blocking = report["mismatch_count"] + report["missing_count"]
        if as_json:
            _emit_json(report)
        else:
            typer.echo(
                f"Verify verified={report['verified_count']} "
                f"mismatch={report['mismatch_count']} "
                f"missing={report['missing_count']} "
                f"legacy={report.get('legacy_count', 0)}"
            )
            typer.echo(f"  summary={report['summary']}")
            for entry in report["mismatched"]:
                fields = ", ".join(d["field"] for d in entry["fields"])
                typer.echo(f"  [MISMATCH] {entry['slug']}: {fields}")
                for d in entry["fields"]:
                    typer.echo(
                        f"      {d['field']}: db={d['db']!r} fs_view={d['fs_view']!r}"
                    )
            for entry in report["missing"]:
                typer.echo(
                    f"  [MISSING] {entry['kind']} {entry['slug']}: {entry['reason']}"
                )
            for entry in report.get("legacy", []):
                # 终态相位的非 blocking 残留：无对应物（reason）或仅审计
                # 字段漂移（fields），与 sync check terminal 宽容一致
                if "reason" in entry:
                    typer.echo(
                        f"  [LEGACY] {entry['kind']} {entry['slug']}: "
                        f"{entry['reason']} phase={entry.get('phase')}"
                    )
                else:
                    fields = ", ".join(d["field"] for d in entry["fields"])
                    typer.echo(
                        f"  [LEGACY] {entry['kind']} {entry['slug']}: "
                        f"audit-field drift ({fields}) phase={entry.get('phase')}"
                    )
        if blocking == 0:
            # LKG 锚点只在对账健康时更新（client-side 记录，非权威）。
            lkg_path = _lkg_path(
                ctx.workspace_root, ctx.content_root, ctx.config.project_key or ""
            )
            _write_lkg_atomic(
                lkg_path,
                {
                    "schema": "fs-migration.last-known-good/v2",
                    "project_id": str(pid),
                    "written_at": report.get("generated_at"),
                    "verified_count": report["verified_count"],
                    "summary": report["summary"],
                },
            )
            if not as_json:
                typer.echo(f"  lkg_written={lkg_path}")
        if blocking:
            raise typer.Exit(2)

    runner._run(action)


@migration_app.command("status")
def migrate_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Project 级 manifest 汇总 + 最近 run（不再只看单 run 的假 0）。"""

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        payload = c.fs_migration_status(pid)
        if as_json:
            _emit_json(payload)
            return
        typer.echo(f"summary={payload['summary']}")
        run = payload.get("latest_run")
        if run is None:
            typer.echo("No migration run yet. Run `map sync migrate scan` first.")
            return
        typer.echo(f"latest run_id={run['run_id']} phase={run['phase']}")
        typer.echo(f"  started_at={run['started_at']} finished_at={run['finished_at']}")

    runner._run(action)
