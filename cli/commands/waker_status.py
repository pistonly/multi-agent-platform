"""``map waker ...`` sub-app — waker 运维视图命令（实验 waker-status-view I3）。

视图只读（A4）：不写日志 / 不发通知 / 不重启 waker / 不改 state。
与 T1-T4 单向流一致（视图是末梢，不闭环回去）。
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path

import typer

waker_app = typer.Typer(help="Waker 运维视图（实验 waker-status-view；只读）")


def _resolve_state_dir(project_root: Path | None) -> Path:
    """Resolve ``.map/`` directory under project_root (or cwd)。"""
    root = project_root or Path.cwd()
    return root / ".map"


@waker_app.callback()
def waker_callback() -> None:
    """Waker 运维视图命令集（只读）。

    强制多命令模式（与 cli/commands/host.py 同款约定）：必须
    ``map waker status`` 才会执行子命令。
    """
    return


@waker_app.command("status")
def waker_status(
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="项目根目录（含 .map/）。默认 cwd。",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="JSON 输出（结构化行 + state 判定阈值；便于脚本消费）。",
    ),
    stale_threshold_seconds: int = typer.Option(
        300,
        "--stale-threshold-seconds",
        help="dead 阈值（秒）。默认 300。",
    ),
) -> None:
    """Render waker 状态视图：persona × 10 字段最小集（live/stale/dead）。

    数据源：
      - .map/simple-waker-state-{persona}.json（waker 自写；实验 I2）
      - 每行派生 state（A2 三档 + busy 卡死升级）
    """
    from cli.waker_status_view import (
        collect_waker_status,
        render_waker_status_table,
    )

    state_dir = _resolve_state_dir(project_root)
    now = datetime.now(timezone.utc)
    rows = collect_waker_status(state_dir, now=now)

    if as_json:
        # 去掉内部字段（_state_file）
        clean = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
        payload = {
            "generated_at": now.isoformat(),
            "stale_threshold_seconds": stale_threshold_seconds,
            "rows": clean,
        }
        typer.echo(_json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    typer.echo(render_waker_status_table(rows))


@waker_app.command("costs")
def waker_costs(
    by: str = typer.Option(
        "persona",
        "--by",
        help="聚合维度：persona（跨实验 per-persona）或 experiment（per-experiment × persona）。",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="项目根目录（含 .map/）。默认 cwd。",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="JSON 输出（结构化；便于脚本消费）。",
    ),
) -> None:
    """per-实验 token 成本跨实验汇总视图（T5-B plan §A4）。

    复用 ``cli/cost_ledger/orchestrator.py``：scan → map → attribute → render。
    价目表暂未接入，统一标 ``pricing_unavailable: true``（plan §A6 不允许 0 兜底）。
    """
    from cli import runner
    from cli.cost_ledger.orchestrator import (
        cost_breakdown_to_yaml_dict,
        persona_aggregate_to_yaml_dict,
        render_experiment_view,
        render_persona_aggregate_view,
    )

    if by not in ("persona", "experiment"):
        typer.echo(
            f"Error: --by must be 'persona' or 'experiment' (got {by!r})", err=True
        )
        raise typer.Exit(1)

    root = project_root or Path.cwd()

    def _action(client) -> None:
        project_id = runner._resolve_project(client, None, None)
        experiments = _fetch_windows(client, project_id)

        if by == "persona":
            aggregate = render_persona_aggregate_view(root, experiments=experiments)
            out = persona_aggregate_to_yaml_dict(aggregate)
            out["pricing_unavailable"] = True
            out["by"] = "persona"
        else:  # by == "experiment"
            breakdowns = [
                render_experiment_view(root, experiment_id=w.experiment_id, experiments=experiments)
                for w in experiments
            ]
            out = {
                "by": "experiment",
                "pricing_unavailable": True,
                "experiments": [
                    cost_breakdown_to_yaml_dict(b, pricing_unavailable=True) for b in breakdowns
                ],
            }

        if as_json:
            typer.echo(_json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
            return

        # YAML-like text output
        if by == "persona":
            _emit_persona_aggregate(out)
        else:
            _emit_experiment_breakdown(out)

    runner._run(_action)


def _fetch_windows(client, project_id) -> list:
    """Fetch experiments and build ``ExperimentWindow`` list。

    与 ``cli/commands/experiment.py:_fetch_experiment_windows`` 同款
    （archived_at/updated_at → ended_at）；不再重复 ended_at=None 抢占
    first_experiment_id 的语义 bug。
    """
    from cli.cost_ledger.attribution import ExperimentWindow

    items = client.list_experiments(project_id, page_size=100)
    windows = []
    for exp in items:
        started_at = exp.created_at.isoformat() if exp.created_at else None
        if not started_at:
            continue
        if exp.archived_at is not None:
            ended_at = exp.archived_at.isoformat()
        elif getattr(exp, "phase", None) is not None and exp.phase.value == "done":
            ended_at = exp.updated_at.isoformat() if exp.updated_at else None
        else:
            ended_at = None
        windows.append(
            ExperimentWindow(
                experiment_id=str(exp.id),
                started_at=started_at,
                ended_at=ended_at,
            )
        )
    return windows


def _emit_persona_aggregate(out: dict) -> None:
    """Render per-persona aggregate as YAML-like text."""
    typer.echo("by: persona")
    typer.echo(f"pricing_unavailable: {out.get('pricing_unavailable', False)}")
    for persona, payload in (out or {}).items():
        if persona == "pricing_unavailable":
            continue
        typer.echo(f"{persona}:")
        if not isinstance(payload, dict):
            continue
        total = payload.get("total")
        if total is not None:
            typer.echo("  total:")
            for k, v in total.items():
                typer.echo(f"    {k}: {v}")
        exp_bd = payload.get("experiment_breakdown") or {}
        if exp_bd:
            typer.echo("  experiment_breakdown:")
            for exp_id, cost in exp_bd.items():
                typer.echo(f"    {exp_id}:")
                for k, v in cost.items():
                    typer.echo(f"      {k}: {v}")


def _emit_experiment_breakdown(out: dict) -> None:
    """Render per-experiment breakdown list as YAML-like text."""
    typer.echo("by: experiment")
    typer.echo(f"pricing_unavailable: {out.get('pricing_unavailable', False)}")
    for exp_payload in out.get("experiments", []) or []:
        typer.echo(f"experiment_id: {exp_payload.get('experiment_id')}")
        typer.echo("  persona_breakdown:")
        for persona, cost in (exp_payload.get("persona_breakdown") or {}).items():
            typer.echo(f"    {persona}:")
            for k, v in cost.items():
                typer.echo(f"      {k}: {v}")
        typer.echo("  match_breakdown:")
        for k, v in (exp_payload.get("match_breakdown") or {}).items():
            typer.echo(f"    {k}: {v}")
        if exp_payload.get("sanity_warning"):
            typer.echo(f"  sanity_warning: {exp_payload['sanity_warning']}")
