"""Lightweight table renderer for list commands.

Renders aligned plain-text tables (no external dependencies) similar to
``kubectl get`` or ``docker ps`` default output. Designed for list
commands where a compact, scannable view is more useful than a full
YAML/JSON dump.

Usage::

    from cli.table_render import render_table

    headers = ["ID", "Title", "Status"]
    rows = [
        ["a1b2c3d4", "Fix proxy issue", "open"],
        ["e5f6g7h8", "Improve CLI output", "open"],
    ]
    print(render_table(headers, rows))

Output::

    ID         TITLE               STATUS
    a1b2c3d4   Fix proxy issue     open
    e5f6g7h8   Improve CLI output  open
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any


def short_uuid(value: uuid.UUID | str | None, length: int = 8) -> str:
    """Render a UUID as a short prefix for table display.

    ``short_uuid(uuid.UUID('a1b2c3d4-...'))`` → ``'a1b2c3d4'``
    Returns ``'-'`` when value is None.
    """
    if value is None:
        return "-"
    text = str(value)
    return text[:length] if len(text) >= length else text


def truncate(text: str | None, max_len: int) -> str:
    """Truncate text to ``max_len`` chars, appending ``...`` if cut.

    Returns ``'-'`` when text is None or empty.
    """
    if not text:
        return "-"
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def format_datetime(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a datetime for table display; ``'-'`` when None."""
    if dt is None:
        return "-"
    return dt.strftime(fmt)


def enum_value(value: Any) -> str:
    """Extract ``.value`` from an Enum, or ``str()`` otherwise."""
    if value is None:
        return "-"
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def render_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    min_col_width: int = 2,
) -> str:
    """Render a list of rows as an aligned plain-text table.

    Column widths are auto-computed from the widest cell in each column
    (header included). Columns are separated by two spaces. No border
    lines — the output is clean and pipe-friendly for shell pipelines.

    Args:
        headers: Column header labels (uppercased in output).
        rows: List of row lists; each row must have ``len == len(headers)``.
        min_col_width: Minimum column width (default 2).

    Returns:
        A single string with ``\\n``-separated lines (no trailing newline).
        Returns ``"(empty)"`` when rows is empty.
    """
    if not rows:
        return "(empty)"

    num_cols = len(headers)
    # Compute column widths from headers and all rows.
    col_widths = [max(min_col_width, len(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                col_widths[i] = max(col_widths[i], len(cell))

    def _format_row(cells: list[str]) -> str:
        return "  ".join(
            cells[i].ljust(col_widths[i]) if i < num_cols else ""
            for i in range(num_cols)
        )

    lines = [_format_row([h.upper() for h in headers])]
    for row in rows:
        lines.append(_format_row(row))
    return "\n".join(lines)
