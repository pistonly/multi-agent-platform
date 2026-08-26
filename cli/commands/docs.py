"""``map docs ...`` sub-app — arch experiment 0519e2a3 PR7.

Repo-bundled documentation surface — no MAP API required.
``docs error-codes`` reads ``docs/error-codes/index.json`` from the
repo root and renders the catalog as a YAML table or filtered JSON.

The repo-root discovery helper (``_resolve_repo_root``) and the index
loader (``_load_error_codes_index``) are co-located here because they
are only used by this command. The loader reads ``_cli_options`` from
``cli.main`` lazily inside the function body to break the
``cli.main ↔ cli.commands.*`` import cycle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
import yaml

from cli.runner import _print_json  # noqa: E402

docs_app = typer.Typer(help="Repo-bundled documentation commands (no API).")


_ERROR_CODES_INDEX_RELATIVE = "docs/error-codes/index.json"

_ERROR_CODES_SEARCH_FIELDS: tuple[str, ...] = (
    "code",
    "category",
    "title",
    "description",
    "hint",
    "docs_url",
    "owner_experiment",
    "since_version",
)


def _resolve_repo_root(start: Path | None = None) -> Path:
    """Walk upward from ``start`` (or cwd) until a directory containing
    ``docs/error-codes/index.json`` is found. Falls back to the start
    directory if not found, so the loader can produce a clean error.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / _ERROR_CODES_INDEX_RELATIVE).is_file():
            return candidate
    return current


def _load_error_codes_index(
    project_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    from cli.main import _cli_options  # runtime state (monkeypatch surface)

    repo_root = _resolve_repo_root(project_root or _cli_options.get("project_root"))
    index_path = repo_root / _ERROR_CODES_INDEX_RELATIVE
    if not index_path.is_file():
        typer.echo(
            f"Error: error codes index not found at {index_path}. "
            "Run from the repo root or pass --project-root.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        raw = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        typer.echo(f"Error: failed to parse {index_path}: {exc}", err=True)
        raise typer.Exit(2) from None
    if not isinstance(raw, dict) or not isinstance(raw.get("codes"), list):
        typer.echo(
            f"Error: {index_path} must be a JSON object with a 'codes' list.",
            err=True,
        )
        raise typer.Exit(2)
    return index_path, raw


def _match_error_code(code_entry: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack_parts: list[str] = []
    for field in _ERROR_CODES_SEARCH_FIELDS:
        value = code_entry.get(field)
        if value is None:
            continue
        haystack_parts.append(str(value))
    haystack = "\n".join(haystack_parts).lower()
    return all(kw.lower() in haystack for kw in keywords)


def _search_keywords_to_list(raw: list[str] | None) -> list[str]:
    """Normalize --search repeats: split comma-separated, strip, drop empties,
    preserve order, dedupe while preserving order.
    """
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        for piece in str(item).split(","):
            kw = piece.strip()
            if not kw or kw in seen:
                continue
            seen.add(kw)
            out.append(kw)
    return out


@docs_app.command("error-codes")
def docs_error_codes(
    search: list[str] | None = typer.Option(
        None,
        "--search",
        help=(
            "Keyword filter (case-insensitive substring match against "
            "code / category / title / description / hint / docs_url / "
            "owner_experiment / since_version). Repeat for AND of "
            "multiple keywords, or comma-separate in one value."
        ),
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing docs/error-codes/index.json (default: walk up from cwd).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw index.json payload (machine-readable) instead of the default YAML table.",
    ),
) -> None:
    """List MAP error codes from ``docs/error-codes/index.json``."""

    index_path, payload = _load_error_codes_index(project_root)
    keywords = _search_keywords_to_list(search)
    codes = payload.get("codes") or []
    matched = [entry for entry in codes if isinstance(entry, dict) and _match_error_code(entry, keywords)]

    if as_json:
        if keywords:
            _print_json({"schema_version": payload.get("schema_version"), "codes": matched})
        else:
            _print_json(payload)
        return

    schema_version = payload.get("schema_version")
    generated_at = payload.get("generated_at")
    owner_experiment = payload.get("owner_experiment")
    header_lines = [
        f"# error codes index: {index_path}",
        f"# schema_version: {schema_version}",
        f"# generated_at: {generated_at}",
        f"# owner_experiment: {owner_experiment}",
    ]
    if keywords:
        header_lines.append(f"# search: {', '.join(keywords)} (AND)")
    header_lines.append(f"# matched: {len(matched)}/{len(codes)}")
    typer.echo("\n".join(header_lines))
    if not matched:
        return
    table_rows: list[list[str]] = []
    for entry in matched:
        table_rows.append(
            [
                str(entry.get("code", "")),
                str(entry.get("category", "")),
                str(entry.get("http_status", "")),
                str(entry.get("title", "")),
                str(entry.get("owner_experiment", "")),
            ]
        )
    header = ["code", "category", "http", "title", "owner_experiment"]
    widths = [max(len(h), *(len(row[i]) for row in table_rows)) for i, h in enumerate(header)]
    sep = "-+-".join("-" * w for w in widths)
    typer.echo(" | ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
    typer.echo(sep)
    for row in table_rows:
        typer.echo(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
