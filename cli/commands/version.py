"""``map version`` — CLI 版本 + 关键命令集→skills 对照范围（3b7c2b44 A6 P3-1）。

P3-1 背景：CLI 版本与 skills 声明的最低要求（``map-plugin.yaml`` 的
``requires``）可能错位。判定「错位」需要人判断（开发期 version 未 bump 是
常态），所以本命令只把**对照范围**暴露给用户，不做酸断言、不建第二张全量漂移表
（全量漂移照旧看 ``map skill list --installed``）。
"""
from __future__ import annotations

import typer

from cli.commands.skill import (
    _get_bundled_skills_dir,
    _read_version,
)

version_app = typer.Typer(help="Show CLI version and key-command skills scope")


def _cli_version() -> str:
    """与 ``map --version`` 同源：map_sdk.__version__（pyproject 单一真相）。"""
    try:
        import map_sdk

        return map_sdk.__version__
    except Exception:
        try:
            from importlib.metadata import version as _dist_version

            return _dist_version("multi-agent-platform")
        except Exception:
            return "unknown"


# 关键命令集（plan A6 P3-1）→ 该命令协作时依赖的 bundled skills。只覆盖范围，
# 不穷举；新命令集接入时按需扩。
_KEY_COMMAND_SKILLS: dict[str, tuple[str, ...]] = {
    "topic": ("map-project-collab", "topic-host", "topic-participant"),
    "bootstrap": ("map-project-collab",),
    "review": ("map-project-collab", "experiment-reviewer", "experiment-host"),
}


def _skills_scope() -> dict[str, dict[str, str]]:
    root = _get_bundled_skills_dir()
    return {
        name: {
            "bundled_version": _read_version(root / name) or "unknown",
            "manifest": str(root / name / "map-plugin.yaml"),
        }
        for name in sorted({s for ss in _KEY_COMMAND_SKILLS.values() for s in ss})
    }


@version_app.command("info")
def version_info(
    as_json: bool = typer.Option(
        False, "--json", help="JSON 输出（CLI 版本 + 关键命令集 skills 对照范围）。"
    ),
) -> None:
    """Show CLI version and the key-command skills scope (topic/bootstrap/review)."""
    import json as _json

    cli_version = _cli_version()
    scope = _skills_scope()
    if as_json:
        payload = {
            "cli": {"version": cli_version, "source": "map_sdk.__version__"},
            "scope": {
                "commands": {cmd: list(skills) for cmd, skills in _KEY_COMMAND_SKILLS.items()},
                "skills": scope,
                "note": (
                    "对照范围=关键命令集 topic/bootstrap/review 所依赖 bundled skills；"
                    "全量漂移看 map skill list --installed"
                ),
            },
        }
        typer.echo(
            _json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        )
        return
    typer.echo(f"map {cli_version}")
    typer.echo("关键命令集→skills 对照范围（全量漂移看 `map skill list --installed`）：")
    for cmd, skills in _KEY_COMMAND_SKILLS.items():
        joined = ", ".join(f"{s}@{scope[s]['bundled_version']}" for s in skills)
        typer.echo(f"  {cmd}: {joined}")
