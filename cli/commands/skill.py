"""``map skill ...`` sub-app — install bundled Skills to user's project.

Commands:
    map skill install   — Copy MAP Skill files from the pip package
                          into the user's project (.cursor/skills/ by default)
    map skill list      — List bundled Skills available for installation
"""
from __future__ import annotations

from pathlib import Path

import typer

skill_app = typer.Typer(help="Manage MAP Skills (install bundled Skills to your project)")

# Directory name inside the cli package
_SKILLS_PACKAGE_DIR = "skills"

# Default install target relative to project root
_DEFAULT_TARGET = ".cursor/skills"


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


@skill_app.command("list")
def skill_list() -> None:
    """List bundled Skills available for installation."""
    from cli.main import _cli_options

    skill_names = _list_skill_dirs()
    if not skill_names:
        typer.echo("No bundled Skills found. This may indicate a broken installation.")
        raise typer.Exit(1)

    fmt = _cli_options.get("format", "yaml")
    skills_root = _get_bundled_skills_dir()

    if fmt == "json":
        import json

        skills_data = [
            {"name": name, "has_skill_md": (skills_root / name / "SKILL.md").exists()}
            for name in skill_names
        ]
        typer.echo(
            json.dumps(
                {"ok": True, "data": {"skills": skills_data}},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(f"{'Skill Name':<30} {'Has SKILL.md'}")
    typer.echo("-" * 50)
    for name in skill_names:
        has_md = (skills_root / name / "SKILL.md").exists()
        typer.echo(f"{name:<30} {'yes' if has_md else 'no'}")


@skill_app.command("install")
def skill_install(
    target: Path = typer.Option(
        Path(_DEFAULT_TARGET),
        "--target",
        "-t",
        help=f"Destination directory (default: {_DEFAULT_TARGET})",
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
    into ``.cursor/skills/`` in the current project. After installation,
    your AI Agent (Cursor, Claude Code, etc.) can read the SKILL.md files
    and follow the MAP collaboration workflow.

    \b
    Examples:
        map skill install                     # Install all Skills
        map skill install -t .map/skills      # Install to .map/skills/
        map skill install -s topic-host        # Install only topic-host
        map skill install --force              # Overwrite without asking
    """
    import shutil

    from cli.main import _cli_options

    fmt = _cli_options.get("format", "yaml")

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
    skipped_list: list[str] = []

    for skill_name in to_install:
        src = skills_root / skill_name
        dst = target / skill_name

        # Check for existing files
        if dst.exists() and not force:
            if fmt != "json":
                typer.echo(f"  Skip: {skill_name}/ (already exists; use --force to overwrite)")
            skipped_count += 1
            skipped_list.append(skill_name)
            continue

        # Remove existing directory if --force
        if dst.exists() and force:
            shutil.rmtree(dst)

        # Copy the entire Skill directory
        shutil.copytree(src, dst)
        installed_count += 1
        installed_list.append(skill_name)

        # Count files copied
        file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
        if fmt != "json":
            typer.echo(f"  Installed: {skill_name}/ ({file_count} file(s))")

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
        "\nNext steps: point your AI Agent to the installed SKILL.md files. "
        "For Cursor, they are automatically discovered from .cursor/skills/."
    )
