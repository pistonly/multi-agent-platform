"""``map skill ...`` sub-app — install bundled Skills to user's project.

v0.11 (M52A/M52B) upgrades:
    - Each bundled Skill ships a ``map-plugin.yaml`` manifest
      (``version`` / ``requires`` / ``runtime_targets``).
    - ``map skill install`` reports installed-vs-bundled version drift
      instead of silently skipping, and accepts ``--runtime`` to target
      ``.cursor/skills`` / ``.claude/skills`` / ``.codex/skills`` / ``./skills``.
    - ``map skill list --installed`` shows version drift per Skill.
    - ``map skill upgrade`` prints a diff summary (added / removed /
      changed files + changed line counts) before overwriting;
      ``--force`` keeps the legacy whole-directory overwrite semantics.
    - Post-install self-check prints the next command to verify the
      chain (``map persona whoami``) and a troubleshooting anchor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

skill_app = typer.Typer(help="Manage MAP Skills (install bundled Skills to your project)")

# Directory name inside the cli package
_SKILLS_PACKAGE_DIR = "skills"

# Default install target relative to project root (backward compatible)
_DEFAULT_TARGET = ".cursor/skills"

# Per-Skill version manifest (M52A)
PLUGIN_MANIFEST = "map-plugin.yaml"

# Runtime → install target mapping (M52B)
RUNTIME_TARGETS: dict[str, str] = {
    "cursor": ".cursor/skills",
    "claude-code": ".claude/skills",
    "codex": ".codex/skills",
    "generic": "skills",
}
_DEFAULT_RUNTIME = "cursor"

# Post-install troubleshooting anchor (relative to the installed Skill dir)
_TROUBLESHOOTING_ANCHOR = (
    "map-project-collab/references/bootstrap-troubleshooting.md"
)


# ---------------------------------------------------------------------------
# Manifest / version helpers
# ---------------------------------------------------------------------------


def _get_bundled_skills_dir() -> Path:
    """Return the path to the bundled skills directory inside the cli package."""
    try:
        # Python 3.9+: importlib.resources.files
        from importlib.resources import files as resource_files

        skills_root = resource_files("cli").joinpath(_SKILLS_PACKAGE_DIR)
        # resource_files may return a Traversable; resolve to a real Path
        # for filesystem operations. When installed from a wheel, the
        # package is unpacked to a real directory, so this always works.
        return Path(str(skills_root))
    except Exception:
        # Fallback: resolve relative to this file
        return Path(__file__).resolve().parent.parent / _SKILLS_PACKAGE_DIR


def _list_skill_dirs() -> list[str]:
    """Return subdirectory names (each is one Skill)."""
    skills_root = _get_bundled_skills_dir()
    if not skills_root.is_dir():
        return []
    return sorted(
        d.name
        for d in skills_root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    )


def _read_manifest(skill_dir: Path) -> dict[str, Any]:
    """Parse ``map-plugin.yaml`` from a Skill directory.

    Returns ``{}`` when the manifest is missing or un-parseable —
    third-party / legacy Skills without a manifest stay installable,
    they just show ``version -`` and ``drift unknown``.
    """
    import yaml

    path = skill_dir / PLUGIN_MANIFEST
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_version(skill_dir: Path) -> str | None:
    version = _read_manifest(skill_dir).get("version")
    if version is None:
        return None
    text = str(version).strip()
    return text or None


def _version_key(version: str) -> tuple[int, ...]:
    """Best-effort numeric tuple for a semver-ish string (``0.11.0`` → ``(0, 11, 0)``)."""
    parts: list[int] = []
    for chunk in str(version).split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _version_cmp(a: str, b: str) -> int:
    ka, kb = _version_key(a), _version_key(b)
    width = max(len(ka), len(kb))
    ka += (0,) * (width - len(ka))
    kb += (0,) * (width - len(kb))
    return (ka > kb) - (ka < kb)


def _drift_label(installed: str | None, bundled: str | None) -> str:
    """Human-readable drift between an installed and the bundled version."""
    if installed is None:
        return "unknown (no manifest)"
    if bundled is None:
        return "unknown (bundled has no manifest)"
    cmp_result = _version_cmp(installed, bundled)
    if cmp_result < 0:
        return f"update available ({installed} → {bundled})"
    if cmp_result > 0:
        return f"installed newer ({installed} > {bundled})"
    return "up-to-date"


def _resolve_target(target: Path | None, runtime: str) -> Path:
    """Resolve the install target directory.

    ``--target`` explicitly given wins; otherwise map ``--runtime``
    (default ``cursor`` → ``.cursor/skills``, backward compatible).
    """
    if target is not None:
        return target
    if runtime not in RUNTIME_TARGETS:
        raise typer.BadParameter(
            f"unknown runtime '{runtime}'. Available: {', '.join(sorted(RUNTIME_TARGETS))}"
        )
    return Path(RUNTIME_TARGETS[runtime])


def _skill_diff(src: Path, dst: Path) -> dict[str, Any]:
    """Diff summary between bundled (``src``) and installed (``dst``) Skill dirs.

    Returns counts of added / removed / changed files plus total added /
    removed lines across changed files (plain text files only).
    """
    import difflib

    def _files(root: Path) -> dict[str, Path]:
        if not root.is_dir():
            return {}
        return {
            str(p.relative_to(root)): p
            for p in root.rglob("*")
            if p.is_file() and ".DS_Store" not in p.parts
        }

    src_files, dst_files = _files(src), _files(dst)
    added = sorted(set(src_files) - set(dst_files))
    removed = sorted(set(dst_files) - set(src_files))
    common = sorted(set(src_files) & set(dst_files))

    changed: list[str] = []
    lines_added = 0
    lines_removed = 0
    for rel in common:
        try:
            src_text = src_files[rel].read_text(encoding="utf-8").splitlines()
            dst_text = dst_files[rel].read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            # Binary or unreadable file: count as changed without line stats.
            changed.append(rel)
            continue
        if src_text == dst_text:
            continue
        changed.append(rel)
        matcher = difflib.SequenceMatcher(a=dst_text, b=src_text)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op in ("delete", "replace"):
                lines_removed += i2 - i1
            if op in ("insert", "replace"):
                lines_added += j2 - j1

    return {
        "added_files": len(added),
        "removed_files": len(removed),
        "changed_files": len(changed),
        "added_lines": lines_added,
        "removed_lines": lines_removed,
        "added_list": added,
        "removed_list": removed,
        "changed_list": changed,
    }


def _echo_diff_summary(skill_name: str, diff: dict[str, Any]) -> None:
    typer.echo(
        f"  {skill_name}: +{diff['added_files']} file(s), "
        f"-{diff['removed_files']} file(s), "
        f"~{diff['changed_files']} file(s) changed "
        f"(+{diff['added_lines']}/-{diff['removed_lines']} lines)"
    )


def _post_install_self_check(target: Path) -> None:
    """M52A 装后自检：输出验证链路的下一步命令与故障定位锚点。"""
    typer.echo(
        "\nSelf-check: verify the chain with:\n"
        "  map --persona host persona whoami"
    )
    if not (Path.cwd() / ".map" / "config.yaml").is_file():
        typer.echo(
            "  [WARN] .map/config.yaml not found in the current directory — "
            "run `map bootstrap --key <project-key> --name \"<Project Name>\" "
            "--api-url http://localhost:8001` first.\n"
            f"  Troubleshooting: {target / _TROUBLESHOOTING_ANCHOR}"
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@skill_app.command("list")
def skill_list(
    installed: bool = typer.Option(
        False,
        "--installed",
        help="Also show installed version and drift vs the bundled version.",
    ),
    target: Path | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Installed-Skills directory to inspect (default: from --runtime).",
    ),
    runtime: str = typer.Option(
        _DEFAULT_RUNTIME,
        "--runtime",
        help="Runtime whose default target to inspect "
        f"(cursor|claude-code|codex|generic; default: {_DEFAULT_RUNTIME}).",
    ),
) -> None:
    """List bundled Skills available for installation."""
    from cli.main import _cli_options

    skill_names = _list_skill_dirs()
    if not skill_names:
        typer.echo("No bundled Skills found. This may indicate a broken installation.")
        raise typer.Exit(1)

    fmt = _cli_options.get("format", "yaml")
    skills_root = _get_bundled_skills_dir()
    target_dir = _resolve_target(target, runtime)

    if fmt == "json":
        import json

        skills_data: list[dict[str, Any]] = []
        for name in skill_names:
            entry: dict[str, Any] = {
                "name": name,
                "has_skill_md": (skills_root / name / "SKILL.md").exists(),
                "version": _read_version(skills_root / name),
            }
            if installed:
                inst = _read_version(target_dir / name)
                entry["installed"] = (target_dir / name).is_dir()
                entry["installed_version"] = inst
                entry["drift"] = _drift_label(inst, entry["version"])
            skills_data.append(entry)
        typer.echo(
            json.dumps(
                {"ok": True, "data": {"skills": skills_data, "target": str(target_dir)}},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if installed:
        typer.echo(f"Target: {target_dir}/")
        typer.echo(
            f"{'Skill Name':<24} {'Bundled':<10} {'Installed':<10} Drift"
        )
        typer.echo("-" * 72)
        for name in skill_names:
            bundled = _read_version(skills_root / name) or "-"
            inst_dir = target_dir / name
            inst = _read_version(inst_dir) if inst_dir.is_dir() else None
            inst_label = inst or ("-" if not inst_dir.is_dir() else "unknown")
            drift = _drift_label(inst, _read_version(skills_root / name)) if inst_dir.is_dir() else "not installed"
            typer.echo(f"{name:<24} {bundled:<10} {inst_label:<10} {drift}")
        return

    typer.echo(f"{'Skill Name':<30} {'Version':<10} {'Has SKILL.md'}")
    typer.echo("-" * 58)
    for name in skill_names:
        has_md = (skills_root / name / "SKILL.md").exists()
        version = _read_version(skills_root / name) or "-"
        typer.echo(f"{name:<30} {version:<10} {'yes' if has_md else 'no'}")


@skill_app.command("install")
def skill_install(
    target: Path | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Destination directory (overrides --runtime; "
        f"default: {RUNTIME_TARGETS[_DEFAULT_RUNTIME]})",
    ),
    runtime: str = typer.Option(
        _DEFAULT_RUNTIME,
        "--runtime",
        help="Install target runtime: cursor|claude-code|codex|generic "
        f"(default: {_DEFAULT_RUNTIME}).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing files without prompting",
    ),
    skill: list[str] | None = typer.Option(
        None,
        "--skill",
        "-s",
        help="Install only the named Skill (can be repeated). Default: all.",
    ),
) -> None:
    """Install MAP Skill files into your project.

    By default, copies all bundled Skill directories from the pip package
    into ``.cursor/skills/`` in the current project (``--runtime`` selects
    ``.claude/skills/`` etc.). After installation, your AI Agent (Cursor,
    Claude Code, etc.) can read the SKILL.md files and follow the MAP
    collaboration workflow.

    \b
    Examples:
        map skill install                            # all Skills → .cursor/skills/
        map skill install --runtime claude-code      # all Skills → .claude/skills/
        map skill install -t .map/skills             # custom directory
        map skill install -s topic-host              # single Skill
        map skill install --force                    # Overwrite without asking
    """
    import shutil

    from cli.main import _cli_options

    fmt = _cli_options.get("format", "yaml")
    target = _resolve_target(target, runtime)

    skills_root = _get_bundled_skills_dir()
    if not skills_root.is_dir():
        typer.echo(
            "Error: bundled Skills directory not found. "
            "This may indicate a broken pip installation.",
            err=True,
        )
        raise typer.Exit(1)

    # Determine which Skills to install
    available = _list_skill_dirs()
    if not available:
        typer.echo("Error: no Skills found to install.", err=True)
        raise typer.Exit(1)

    if skill:
        # Validate requested skill names
        missing = [s for s in skill if s not in available]
        if missing:
            typer.echo(
                f"Error: unknown Skill(s): {', '.join(missing)}. "
                f"Available: {', '.join(available)}",
                err=True,
            )
            raise typer.Exit(1)
        to_install = skill
    else:
        to_install = available

    # Create target directory
    target.mkdir(parents=True, exist_ok=True)

    installed_count = 0
    skipped_count = 0
    installed_list: list[str] = []
    skipped_list: list[dict[str, Any]] = []

    for skill_name in to_install:
        src = skills_root / skill_name
        dst = target / skill_name

        # Check for existing files: no silent skip — show version drift
        # and the upgrade command instead (M52A).
        if dst.exists() and not force:
            installed_version = _read_version(dst)
            bundled_version = _read_version(src)
            if fmt != "json":
                typer.echo(
                    f"  Skip: {skill_name}/ (already exists"
                    + (
                        f", installed {installed_version}, bundled {bundled_version}"
                        if installed_version or bundled_version
                        else ""
                    )
                    + "; run 'map skill upgrade' to update, or --force to overwrite)"
                )
            skipped_count += 1
            skipped_list.append(
                {
                    "name": skill_name,
                    "installed_version": installed_version,
                    "bundled_version": bundled_version,
                    "hint": "map skill upgrade",
                }
            )
            continue

        # Remove existing directory if --force
        if dst.exists() and force:
            shutil.rmtree(dst)

        # Copy the entire Skill directory (map-plugin.yaml comes along
        # as the installed version manifest)
        shutil.copytree(src, dst)
        installed_count += 1
        installed_list.append(skill_name)

        # Count files copied
        file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
        if fmt != "json":
            version = _read_version(dst) or "-"
            typer.echo(f"  Installed: {skill_name}/ ({file_count} file(s), v{version})")

    if fmt == "json":
        import json

        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "installed": installed_list,
                        "skipped": skipped_list,
                        "installed_count": installed_count,
                        "skipped_count": skipped_count,
                        "target": str(target),
                        "next_step": "map --persona host persona whoami",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(
        f"\nDone: {installed_count} Skill(s) installed to {target}/"
        + (f", {skipped_count} skipped" if skipped_count else "")
    )
    typer.echo(
        "Next steps: point your AI Agent to the installed SKILL.md files. "
        "For Cursor, they are automatically discovered from .cursor/skills/."
    )
    _post_install_self_check(target)


@skill_app.command("upgrade")
def skill_upgrade(
    target: Path | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Directory holding installed Skills (overrides --runtime; "
        f"default: {RUNTIME_TARGETS[_DEFAULT_RUNTIME]})",
    ),
    runtime: str = typer.Option(
        _DEFAULT_RUNTIME,
        "--runtime",
        help="Runtime whose target to upgrade "
        f"(cursor|claude-code|codex|generic; default: {_DEFAULT_RUNTIME}).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip the diff summary; overwrite every installed Skill "
        "(legacy whole-directory semantics).",
    ),
    skill: list[str] | None = typer.Option(
        None,
        "--skill",
        "-s",
        help="Upgrade only the named Skill (can be repeated). Default: all installed.",
    ),
) -> None:
    """Upgrade installed Skills to the bundled version.

    Prints a diff summary (added / removed / changed files and line
    counts) per Skill before overwriting. Skills with no differences
    are skipped; not-yet-installed Skills are reported with an install
    hint. ``--force`` keeps the legacy semantics: no diff, overwrite
    everything.
    """
    import shutil

    from cli.main import _cli_options

    fmt = _cli_options.get("format", "yaml")
    target = _resolve_target(target, runtime)

    skills_root = _get_bundled_skills_dir()
    if not skills_root.is_dir():
        typer.echo(
            "Error: bundled Skills directory not found. "
            "This may indicate a broken pip installation.",
            err=True,
        )
        raise typer.Exit(1)

    available = _list_skill_dirs()
    if skill:
        missing = [s for s in skill if s not in available]
        if missing:
            typer.echo(
                f"Error: unknown Skill(s): {', '.join(missing)}. "
                f"Available: {', '.join(available)}",
                err=True,
            )
            raise typer.Exit(1)
        candidates = skill
    else:
        candidates = available

    if not target.is_dir():
        typer.echo(
            f"Error: target directory {target}/ does not exist. "
            "Run 'map skill install' first.",
            err=True,
        )
        raise typer.Exit(1)

    upgraded_list: list[dict[str, Any]] = []
    unchanged_list: list[str] = []
    not_installed_list: list[str] = []

    if fmt != "json":
        typer.echo(f"Upgrading Skills in {target}/ ...")
    for skill_name in candidates:
        src = skills_root / skill_name
        dst = target / skill_name

        if not dst.is_dir():
            not_installed_list.append(skill_name)
            if fmt != "json":
                typer.echo(f"  Not installed: {skill_name}/ (use 'map skill install')")
            continue

        if not force:
            diff = _skill_diff(src, dst)
            if (
                diff["added_files"] == 0
                and diff["removed_files"] == 0
                and diff["changed_files"] == 0
            ):
                unchanged_list.append(skill_name)
                if fmt != "json":
                    version = _read_version(dst) or "-"
                    typer.echo(f"  Up-to-date: {skill_name}/ (v{version}, no diff)")
                continue
            if fmt != "json":
                installed_version = _read_version(dst) or "-"
                bundled_version = _read_version(src) or "-"
                typer.echo(
                    f"  Diff: {skill_name}/ (installed v{installed_version} "
                    f"→ bundled v{bundled_version})"
                )
                _echo_diff_summary(skill_name, diff)
        else:
            diff = None

        shutil.rmtree(dst)
        shutil.copytree(src, dst)
        version = _read_version(dst) or "-"
        upgraded_list.append(
            {
                "name": skill_name,
                "version": version,
                "diff": diff,
            }
        )
        if fmt != "json":
            typer.echo(f"  Upgraded: {skill_name}/ → v{version}")

    if fmt == "json":
        import json

        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "upgraded": [u["name"] for u in upgraded_list],
                        "upgraded_count": len(upgraded_list),
                        "unchanged": unchanged_list,
                        "not_installed": not_installed_list,
                        "target": str(target),
                        "next_step": "map --persona host persona whoami",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(
        f"\nDone: {len(upgraded_list)} Skill(s) upgraded in {target}/"
        + (f", {len(unchanged_list)} up-to-date" if unchanged_list else "")
        + (f", {len(not_installed_list)} not installed" if not_installed_list else "")
    )
    _post_install_self_check(target)
