"""``map fs verify-audit`` CLI 子命令 (实验 e6d23886 I3)。

T7-a verify-audit 检测层 CLI 入口：扫描 map/topics/ + map/experiments/ 的
index.md + audit.jsonl, 检测 5 类 audit 链漂移 (D001-D005)。

注册到 main.py: ``app.add_typer(verify_audit_app, name="fs")`` (嵌套路径: map fs verify-audit)

设计要点:
- 只读: 不写 audit.jsonl (防递归绕过, 选项 A 工程实现)
- 双格式: --format json (默认) + --format human
- exit code 0/1/2 由 cli/verify_audit/output.compute_exit_code 计算
"""
from __future__ import annotations

from pathlib import Path

import typer

from cli.verify_audit.output import compute_exit_code, print_output
from cli.verify_audit.scanner import _audit_corrupted, scan_plane_audit

verify_audit_app = typer.Typer(
    add_completion=False,
    help=(
        "verify-audit: detect audit chain drift in map/topics/ + map/experiments/. "
        "Read-only; surfaces 5 drift kinds (D001-D005) for host/supervisor review."
    ),
)


@verify_audit_app.command("verify-audit")
def fs_verify_audit(
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root (defaults to .map/ parent).",
    ),
    output_format: str = typer.Option(
        "json",
        "--output-format",
        help="Output format: json (default, waker-friendly) or human (table).",
    ),
) -> None:
    """扫描本地 FS plane 的 audit 链漂移, 输出 drift_id + 路径 + 字段。

    退出码:
      0 = 干净
      1 = 有漂移 (drift_detected)
      2 = 校验过程异常 (audit.jsonl 损坏)
    """
    if workspace is None:
        from cli.commands.fs import _workspace

        workspace = _workspace()
    if not workspace.is_dir():
        typer.echo(f"Error: workspace not found: {workspace}", err=True)
        raise typer.Exit(2)

    detector = scan_plane_audit(workspace)
    corrupted = [str(p.relative_to(workspace)) for p in _audit_corrupted(workspace)]
    print_output(
        detector,
        fmt=output_format,
        workspace_root=str(workspace),
        corrupted_files=corrupted,
    )
    raise typer.Exit(compute_exit_code(detector, corrupted_files=corrupted))
