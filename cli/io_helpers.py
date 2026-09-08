"""CLI 文件读取辅助（T23 从 commands/experiment.py / main.py 收敛）。

``_read_text_file`` 原定义在 ``cli/commands/experiment.py``（size-cap
搬迁产物），又被 ``cli/main.py`` re-export 回去供其他 commands 使用——
是 main ↔ commands 循环 import 的成因之一。T23 将其独立成家：

- ``cli/commands/experiment.py``、``cli/main.py``（re-export 兼容层）、
  ``cli/runner.py``（``_load_topic_resolve_payload``）均从本模块导入；
- 本模块零依赖（仅 typer），任何 commands 顶层导入都不会触发环。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer
import yaml

# Keep implicit stdin input bounded. Large plans/logs should use --file so
# callers do not accidentally buffer an unbounded pipe into the CLI process.
MAX_PIPED_TEXT_CHARS = 1_048_576


def _read_text_file(path: Path, *, kind: str) -> str:
    """Read a required ``--file``/``--metadata`` argument.

    Converts missing-file and not-a-file OS errors into a clean CLI error
    (exit code 2) instead of letting Python emit a raw traceback, so that
    user-facing mistakes like ``--metadata ./missing.yaml`` stay legible.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        typer.echo(f"Error: {kind} file not found: {path}", err=True)
        raise typer.Exit(2) from None
    except IsADirectoryError:
        typer.echo(f"Error: {kind} path is a directory, not a file: {path}", err=True)
        raise typer.Exit(2) from None


def _read_piped_text(*, kind: str) -> str | None:
    """Read text from stdin only when stdin is not an interactive terminal.

    Commands such as ``topic comment`` can therefore accept
    ``cat note.md | map topic comment --topic demo`` without a dedicated
    stdin flag. A TTY is never read, so an interactive invocation without an
    explicit content source fails immediately instead of blocking.

    Explicit ``--body``/``--file`` precedence is handled by each caller.
    Shell quoting still applies to the command that produces stdin; this
    helper only prevents the shell from reparsing content after it reaches the
    CLI.
    """
    try:
        if sys.stdin.isatty():
            return None
        content = sys.stdin.read(MAX_PIPED_TEXT_CHARS + 1)
    except (OSError, UnicodeError) as exc:
        typer.echo(f"Error: failed to read {kind} from stdin: {exc}", err=True)
        raise typer.Exit(2) from None

    if len(content) > MAX_PIPED_TEXT_CHARS:
        typer.echo(
            f"Error: piped {kind} exceeds {MAX_PIPED_TEXT_CHARS} characters; "
            "use --file instead.",
            err=True,
        )
        raise typer.Exit(2)
    return content or None


def _read_yaml_file(path: Path | None, *, kind: str = "metadata") -> Any:
    if path is None:
        return None
    return yaml.safe_load(_read_text_file(path, kind=kind))
