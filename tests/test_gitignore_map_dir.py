"""`.map/` is local runtime and must stay fully gitignored."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_dot_map_fully_gitignored() -> None:
    """No allow-list: config.yaml, agents.yaml, baselines, history all stay local."""
    gitignore = _repo_root() / ".gitignore"
    assert gitignore.is_file()
    patterns: list[str] = []
    for raw in gitignore.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ".map" in line:
            patterns.append(line)
    assert any(p in {".map/", ".map", ".map/**"} for p in patterns), (
        ".gitignore must ignore the whole .map/ directory; found "
        f"{patterns!r}"
    )
    allow = [p for p in patterns if p.startswith("!.map")]
    assert not allow, f".map/ must not have gitignore exceptions: {allow}"
