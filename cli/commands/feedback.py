"""``map feedback ...`` sub-app — retired in v0.15 M62 (dead-letter box teardown).

全链路废弃定案见话题 v015-feedback-deprecation-design（round2 全票）。
四命令统一引导性拒绝（exit 2，M58 ``_DB_WRITE_RETIRED`` 同款模式）：
自托管收件人错位 + 替代通道已存在。历史数据只读保留于平台 DB，
人类友好索引 = 实验 m62-feedback-deprecation 清账处置表。
"""

from __future__ import annotations

import typer

feedback_app = typer.Typer(help="Platform feedback inbox commands (retired in v0.15 M62)")

_HINT = (
    "Platform feedback was retired in v0.15 M62 (dead-letter box: no recipient on "
    "self-hosted deployments, superseded by better channels).\n"
    "- bug report: open a GitHub issue (repo link in README; attach repro steps)\n"
    "- improvement idea / dogfood feedback: ask the host to open a MAP topic\n"
    "Historical feedback records are preserved read-only in the platform DB."
)


def _feedback_retired(command: str) -> None:
    typer.echo(f"Error: `map feedback {command}` is retired (v0.15 M62).\n{_HINT}", err=True)
    raise typer.Exit(2)


@feedback_app.command("submit")
def feedback_submit(
    body: str | None = typer.Option(None, "--body", help="Retained for shell compat; command always exits 2."),
    body_file: str | None = typer.Option(None, "--file", help="Retained for shell compat; command always exits 2."),
) -> None:
    _feedback_retired("submit")


@feedback_app.command("list")
def feedback_list(
    status: str | None = typer.Option(None, "--status"),
    category: str | None = typer.Option(None, "--category"),
    project: str | None = typer.Option(None, "--project"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1, max=200),
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    _feedback_retired("list")


@feedback_app.command("get")
def feedback_get(feedback_id: str = typer.Argument(..., help="Retained for shell compat; command always exits 2.")) -> None:
    _feedback_retired("get")


@feedback_app.command("update")
def feedback_update(
    feedback_id: str = typer.Argument(..., help="Retained for shell compat; command always exits 2."),
    status: str | None = typer.Option(None, "--status"),
    category: str | None = typer.Option(None, "--category"),
    archived: bool | None = typer.Option(None, "--archived/--no-archived"),
) -> None:
    _feedback_retired("update")
