"""``map usage summary`` — CLI 记账聚合查询（实验 4e4206de I7）。

单一机器可判路径：读取 ``.map/usage/cli-calls.jsonl``，按 persona 聚合 ``--since``
会话窗口内的调用数与输出字节总量，输出 ``{persona, work_calls, total_output_bytes}``。
本地读取，不调 API。
"""

from __future__ import annotations

import json

import typer

from cli.usage_ledger import (
    ledger_path,
    parse_since,
    read_records,
    resolve_map_dir,
    summarize,
)

usage_app = typer.Typer(help="CLI usage accounting (measurement surface).")


@usage_app.command("summary")
def usage_summary(
    since: str | None = typer.Option(
        None,
        "--since",
        help="ISO8601 下界（含）：只统计该时刻起的调用；省略则统计全量。",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="输出机器可读 JSON 数组 [{persona, work_calls, total_output_bytes}]。",
    ),
) -> None:
    """Summarize recorded CLI calls per persona within an optional window."""
    since_dt = None
    if since is not None:
        try:
            since_dt = parse_since(since)
        except ValueError as exc:
            typer.echo(f"Error: invalid --since {since!r}: {exc}", err=True)
            raise typer.Exit(2) from exc

    map_dir = resolve_map_dir()
    if map_dir is None:
        typer.echo(
            "Error: no .map/ found — run inside a bootstrapped MAP project.",
            err=True,
        )
        raise typer.Exit(1)

    rows = summarize(list(read_records(map_dir)), since=since_dt)

    if as_json:
        typer.echo(json.dumps([r.to_dict() for r in rows], ensure_ascii=False))
        return

    if not rows:
        window = f" since {since}" if since else ""
        typer.echo(f"no CLI calls recorded{window} ({ledger_path(map_dir)})")
        return

    typer.echo("persona            work_calls  total_output_bytes")
    for row in rows:
        typer.echo(
            f"{row.persona:<18} {row.work_calls:>10}  {row.total_output_bytes:>18}"
        )
