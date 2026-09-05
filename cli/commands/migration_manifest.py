"""``map sync migrate ...`` sub-app —— DB → FS projection 存量迁移（实验 M2 I5+I6：A5+A6）。

4 阶段 + 1 status 子命令，对应 server-side ``migration_manifest_service``：

- ``scan`` —— 枚举 DB experiments + topics，建 ``migration_manifest_items`` 行；
  重跑幂等（idempotency_key UNIQUE）。
- ``dry-run`` —— 算 plan，不 apply；列 pending + stale-reset 项。
- ``execute`` —— 逐 item apply；按 stale 6 类分类失败；attempts 超限进 failed 终态。
- ``verify`` —— 复用 ``map sync --check`` + last-known-good diff（hash drift / DB 删了 / 新增）。
- ``status`` —— 最近 run 的 summary + LKG 是否存在。

LKG 落点：``<content_root>/.fs-migration/last-known-good.json``，由 ``verify`` 读、
``finish_run`` 后写。

CLI 不直接做 HTTP CAS apply（execute 阶段当前只更新 manifest status；apply
实现在 SDK ``apply_delta_with_retry``；本期 I6 阶段先把 status 管线跑通，
end-to-end apply 留 A7 收口）。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import typer
from sqlalchemy import select

from cli import runner
from server.db.session import SessionLocal
from server.domain.models import MigrationManifestItem
from server.services import migration_manifest_service as svc

migration_app = typer.Typer(
    help=(
        "DB → FS projection 存量迁移 manifest（实验 M2 I5+I6）。4 阶段："
        "scan / dry-run / execute / verify，加 status 子命令。"
    ),
    no_args_is_help=True,
)


def _resolve_project_uuid(c, project: uuid.UUID | None, project_key: str | None) -> uuid.UUID:
    """复用 sync.publish 的 project 解析逻辑。"""
    return runner._resolve_project(c, project, project_key)


def _emit_json(obj: object) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


@migration_app.command("scan")
def migrate_scan(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Scan 阶段：枚举 DB experiments + topics，建 manifest item 行（幂等）。"""
    from map_client.client import MAPClient

    def action(c: MAPClient):
        pid = _resolve_project_uuid(c, project, project_key)
        db = SessionLocal()
        try:
            report = svc.scan_project(db, project_id=pid)
            db.commit()
            payload = {
                "project_id": str(pid),
                "run_id": report.run_id,
                "scanned": report.scanned,
                "inserted": report.inserted,
                "skipped_existing": report.skipped_existing,
                "by_kind": report.by_kind,
                "by_status": report.by_status,
            }
            if as_json:
                _emit_json(payload)
            else:
                typer.echo(f"Scan run_id={report.run_id}")
                typer.echo(f"  scanned={report.scanned} inserted={report.inserted} "
                           f"skipped_existing={report.skipped_existing}")
                typer.echo(f"  by_kind={report.by_kind}")
                typer.echo(f"  by_status={report.by_status}")
        finally:
            db.close()
        return None

    runner._run(action)


@migration_app.command("dry-run")
def migrate_dry_run(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    limit: int = typer.Option(50, "--limit", help="最多展示的 actionable item 数"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Dry-run 阶段：scan + 列 actionable 项，不 apply。"""
    from map_client.client import MAPClient

    def action(c: MAPClient):
        pid = _resolve_project_uuid(c, project, project_key)
        db = SessionLocal()
        try:
            scan = svc.scan_project(db, project_id=pid)
            stale = svc.reset_stale_in_flight(db, run_id=scan.run_id)
            db.commit()
            # 全 project actionable（含旧 run 漏掉的），不是「本次新 scan
            # 又扫到几个」（重跑通常 idempotent，新 run 没新 item）。
            items = svc.list_project_actionable(db, project_id=pid, limit=limit)
            payload = {
                "project_id": str(pid),
                "run_id": scan.run_id,
                "scanned": scan.scanned,
                "stale_in_flight_reset": stale,
                "actionable_count": len(items),
                "actionable": [
                    {
                        "id": it.id,
                        "kind": it.kind,
                        "slug": it.slug,
                        "content_hash": it.content_hash,
                        "attempts": it.attempts,
                    }
                    for it in items
                ],
            }
            if as_json:
                _emit_json(payload)
            else:
                typer.echo(f"Dry-run run_id={scan.run_id}")
                typer.echo(f"  scanned={scan.scanned} stale_in_flight_reset={stale} "
                           f"actionable={len(items)}")
                for it in items:
                    typer.echo(f"  - [{it.kind}] {it.slug}  hash={it.content_hash[:12]}…  "
                               f"attempts={it.attempts}")
        finally:
            db.close()
        return None

    runner._run(action)


@migration_app.command("execute")
def migrate_execute(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    limit: int = typer.Option(50, "--limit", help="本轮最多执行的 item 数"),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="实际调用 /fs/projection/delta CAS apply；不带则只 claim+mark_applied 不调 server",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Execute 阶段：逐 item claim + (可选) apply + mark。

    默认 ``--apply=false`` 仅做 client-side 簿记（claim → mark_applied 跳过 server）；
    这是 I6 阶段的安全默认 —— host 跑 dry-run → review → ``--apply=true``。
    """
    from map_client.client import MAPClient

    def action(c: MAPClient):
        pid = _resolve_project_uuid(c, project, project_key)
        db = SessionLocal()
        try:
            scan = svc.scan_project(db, project_id=pid)
            stale = svc.reset_stale_in_flight(db, run_id=scan.run_id)
            db.commit()
            # 全 project actionable（含旧 run 漏掉的）
            items = svc.list_project_actionable(db, project_id=pid, limit=limit)
            processed = 0
            failed = 0
            for it in items:
                claimed = svc.claim(db, item_id=it.id)
                if claimed is None:
                    continue  # 已被抢
                try:
                    if apply:
                        # 真 apply 路径：本里程碑 I6 阶段留 placeholder；
                        # 实际 SDK 调用留 A7 收口（end-to-end live evidence）。
                        # 这里只更新 manifest status，不调 server CAS。
                        # host 风险提示要求先演练再谈全量，先把 status 管线跑通。
                        raise RuntimeError(
                            "execute --apply=true 尚未实现 end-to-end CAS（实验 M2 A7 收口阶段补）"
                        )
                    else:
                        # dry execute：仅 mark_applied（演练 status 管线）
                        svc.mark_applied(db, item_id=claimed.id)
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    # stale-aware 失败：分类 + 拼 prefix 到 last_error
                    svc.mark_failed_with_stale(
                        db, item_id=claimed.id, error=str(exc)[:256], exc=exc
                    )
                    failed += 1
            db.commit()
            # 跨 run execute：summary 用 project-wide 聚合，否则 latest run
            # 看起来 0（实际 claim 的都是老 run 的 item）
            summary = svc.summarize_project(db, project_id=pid)
            svc.finish_run(db, run_id=scan.run_id, summary=summary)
            db.commit()
            payload = {
                "project_id": str(pid),
                "run_id": scan.run_id,
                "apply": apply,
                "stale_in_flight_reset": stale,
                "processed": processed,
                "failed": failed,
                "summary": summary,
            }
            if as_json:
                _emit_json(payload)
            else:
                typer.echo(f"Execute run_id={scan.run_id} apply={apply}")
                typer.echo(f"  stale_in_flight_reset={stale} processed={processed} "
                           f"failed={failed}")
                typer.echo(f"  summary={summary}")
        finally:
            db.close()
        return None

    runner._run(action)


@migration_app.command("verify")
def migrate_verify(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Verify 阶段：scan + 与 last-known-good.json diff；写新 LKG。"""
    from map_client.client import MAPClient

    from cli.project_context import ProjectRootNotFoundError, current_context

    try:
        ctx = current_context()
    except ProjectRootNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    lkg_path = Path(ctx.workspace_root) / ctx.content_root / svc.LKG_RELATIVE_PATH
    lkg_payload: dict | None = None
    if lkg_path.is_file():
        try:
            lkg_payload = json.loads(lkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lkg_payload = None

    def action(c: MAPClient):
        pid = _resolve_project_uuid(c, project, project_key)
        db = SessionLocal()
        try:
            scan = svc.scan_project(db, project_id=pid)
            db.commit()
            # diff 用 project-wide applied item（跨 run 聚合）。
            # LKG 也用同样视角；两边对齐后才能拿到「in_both 健康 / in_lkg_only 漂移
            # / in_db_only 新增」的真值。当前 run 0 item 不影响 diff。
            all_applied = list(
                db.scalars(
                    select(MigrationManifestItem).where(
                        MigrationManifestItem.project_id == pid,
                        MigrationManifestItem.status == "applied",
                    )
                )
            )
            diff = svc.diff_against_lkg(db_items=all_applied, lkg=lkg_payload)

            new_lkg_items = all_applied
            if new_lkg_items:
                lkg_path.parent.mkdir(parents=True, exist_ok=True)
                lkg_path.write_text(
                    json.dumps(
                        svc.build_lkg_payload(
                            project_id=pid, run_id=scan.run_id, items=new_lkg_items
                        ),
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    ),
                    encoding="utf-8",
                )

            payload = {
                "project_id": str(pid),
                "run_id": scan.run_id,
                "lkg_path": str(lkg_path),
                "lkg_present_before": diff["lkg_present"],
                "lkg_run_id_before": diff["lkg_run_id"],
                "diff": diff,
                "lkg_written": bool(new_lkg_items),
                "lkg_item_count": len(new_lkg_items),
            }
            if as_json:
                _emit_json(payload)
            else:
                typer.echo(f"Verify run_id={scan.run_id}")
                typer.echo(f"  lkg_present_before={diff['lkg_present']} "
                           f"lkg_run_id_before={diff['lkg_run_id']}")
                typer.echo(f"  diff summary={diff['summary']}")
                for bucket in ("in_lkg_only", "in_db_only", "hash_drift"):
                    for it in diff[bucket]:
                        typer.echo(f"  [{bucket}] {it.get('kind')} {it.get('slug')} "
                                   f"hash={it.get('content_hash', '?')[:12]}")
                typer.echo(f"  lkg_written={payload['lkg_written']} "
                           f"lkg_item_count={payload['lkg_item_count']}")
        finally:
            db.close()
        return None

    runner._run(action)


@migration_app.command("status")
def migrate_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """最近一次 manifest run 的 summary + LKG 状态。"""
    from map_client.client import MAPClient

    def action(c: MAPClient):
        pid = _resolve_project_uuid(c, project, project_key)
        db = SessionLocal()
        try:
            run = svc.get_latest_run(db, project_id=pid)
            if run is None:
                typer.echo("No migration run yet. Run `map sync migrate scan` first.")
                raise typer.Exit(0)
            summary = svc.summarize_run(db, run_id=run.id)
            payload = {
                "project_id": str(pid),
                "run_id": run.id,
                "phase": run.phase,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "summary": summary,
            }
            if as_json:
                _emit_json(payload)
            else:
                typer.echo(f"Latest run_id={run.id} phase={run.phase}")
                typer.echo(f"  started_at={payload['started_at']} "
                           f"finished_at={payload['finished_at']}")
                typer.echo(f"  summary={summary}")
        finally:
            db.close()
        return None

    runner._run(action)
