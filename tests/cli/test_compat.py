"""CLI surface compatibility snapshot (arch experiment 0519e2a3 PR8).

Regression guard for the cli/main.py monolith split (topic 68e20e8a).
Pins the public CLI surface — top-level sub-app registry, command names
per sub-app, and the cli/main.py / cli/commands/ file shape — so any
unintended CLI regression shows up as a clear test diff instead of a
broken ``map --help`` in production.

**Update procedure** when intentionally changing the CLI surface:

1. Edit the matching ``EXPECTED_*`` literal in this file.
2. If you added a new sub-app, also add the module to
   ``EXPECTED_SUBAPP_FILES``.
3. Run ``pytest tests/cli/test_compat.py -v`` to confirm green.
4. Bump the line-count cap with a one-line commit note explaining why.

The test imports ``cli.main.app`` directly and uses ``typer.testing.CliRunner``
to invoke ``--help`` — no API server, no subprocess, < 100 ms per case.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_MAIN_PY = REPO_ROOT / "cli" / "main.py"
CLI_COMMANDS_DIR = REPO_ROOT / "cli" / "commands"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_commands_block(help_text: str) -> list[str]:
    """Extract top-level command names from a Typer ``--help`` render.

    Typer renders commands in a 2-column table after a ``Commands:``
    header. The first whitespace-separated token of each non-blank row
    in that block is the command name.
    """
    in_commands = False
    out: list[str] = []
    for line in help_text.splitlines():
        if line.strip().startswith("Commands:"):
            in_commands = True
            continue
        if not in_commands:
            continue
        stripped = line.strip()
        if not stripped:
            break  # blank line ends the section
        tokens = stripped.split(None, 1)
        if tokens:
            out.append(tokens[0])
    return out


# ---------------------------------------------------------------------------
# top-level sub-app registry
# ---------------------------------------------------------------------------


EXPECTED_TOP_LEVEL_SUBAPPS = [
    # resource-domain sub-apps (all registered via app.add_typer in cli/main.py)
    "project",
    "experiment",
    "persona",
    "runtime",
    "agent",
    "notification",
    "inbound-event",
    "audit",
    "topic",
    "mention",
    "todo",
    "action",
    "feedback",
    "fs",
    "docs",
]


def test_top_level_subapps_match_snapshot(runner: CliRunner) -> None:
    """Every resource-domain sub-app is wired via ``app.add_typer``."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.stdout
    actual = _parse_commands_block(result.stdout)
    for name in EXPECTED_TOP_LEVEL_SUBAPPS:
        assert name in actual, f"sub-app {name!r} missing from `map --help`; got {actual}"


# ---------------------------------------------------------------------------
# per sub-app command surface
# ---------------------------------------------------------------------------


EXPECTED_SUBAPP_COMMANDS: dict[str, list[str]] = {
    "action": [
        "list",
        "complete",
        "deliver",
        "cancel",
        "link",
        "mark-wake-sent",
        "mark-stale",
    ],
    "feedback": ["submit", "list", "get", "update"],
    "fs": [
        "init",
        "topic-create",
        "comment",
        "list",
        "show",
        "work",
        "advance-round",
        "close",
        "migrate-from-docs",
        "status",
        "push",
        "diff",
        "sync",
        "archive",
        "archive-index",
    ],
    "docs": ["error-codes"],
    "topic": [
        "create",
        "list",
        "show",
        "progress",
        "resolve",
        "advance-round",
        "rollback-round",
        "comment",
        "close",
        "reopen",
        "dismiss",
        "read",
        "mark-seen",
        "archive",
        "migrate",
        # plan v3 I4: FS 话题执行项子组（topic_app.add_typer(action_item_app)）
        "action-item",
    ],
    "mention": ["dismiss", "list", "dismiss-all", "reconcile-stale"],
    "todo": ["clear"],
    "notification": ["list", "read", "read-all"],
    "inbound-event": ["record"],
    "audit": ["list"],
}


@pytest.mark.parametrize(
    ("subapp", "expected"),
    sorted(EXPECTED_SUBAPP_COMMANDS.items()),
    ids=sorted(EXPECTED_SUBAPP_COMMANDS),
)
def test_subapp_commands_match_snapshot(
    runner: CliRunner, subapp: str, expected: list[str]
) -> None:
    """Each sub-app exposes the expected command surface (no silent drop / rename)."""
    result = runner.invoke(app, [subapp, "--help"])
    assert result.exit_code == 0, result.stdout
    actual = _parse_commands_block(result.stdout)
    assert sorted(actual) == sorted(expected), (
        f"sub-app {subapp!r} command drift: "
        f"expected {sorted(expected)}, got {sorted(actual)}"
    )


# ---------------------------------------------------------------------------
# cli/main.py monolith size guard
# ---------------------------------------------------------------------------


# cli/main.py shrank 2967 → 2200 across PR3-PR7, then ~1400 after
# experiment/project/persona/runtime split. Bumped from 1450 → 1600 after
# adding the `dashboard` command (~130 lines) and `--format table` support
# (~25 lines in _run). After 369ccac the package split left main.py at 1662
# (over cap); the persona-compare rendering block (~275 lines) moved to
# cli/persona_compare.py bringing it back to ~1400. Hold the line at
# <= 1600 — intentional growth requires bumping this + a commit note
# explaining why.
MAX_CLI_MAIN_PY_LINES = 1600


def test_cli_main_py_under_size_cap() -> None:
    """Monolith regression guard: ``cli/main.py`` must stay under 1600 lines."""
    actual = sum(1 for _ in CLI_MAIN_PY.open(encoding="utf-8"))
    assert actual <= MAX_CLI_MAIN_PY_LINES, (
        f"cli/main.py grew to {actual} lines (cap={MAX_CLI_MAIN_PY_LINES}); "
        "consider splitting more sub-apps into cli/commands/."
    )


# ---------------------------------------------------------------------------
# cli/commands/ file inventory
# ---------------------------------------------------------------------------


EXPECTED_SUBAPP_FILES = [
    "__init__.py",
    "action.py",
    "agent.py",
    "audit.py",
    "auth.py",
    "docs.py",
    "experiment.py",
    "feedback.py",
    "fs.py",
    "host.py",
    "notification.py",
    "persona.py",
    "project.py",
    "runtime.py",
    "server.py",
    "skill.py",
    "sync.py",
    "topic.py",
]


def test_cli_commands_directory_inventory() -> None:
    """``cli/commands/`` holds exactly the expected resource-domain modules."""
    actual = sorted(p.name for p in CLI_COMMANDS_DIR.glob("*.py"))
    assert actual == sorted(EXPECTED_SUBAPP_FILES), (
        f"cli/commands/ drift: "
        f"expected {sorted(EXPECTED_SUBAPP_FILES)}, got {actual}"
    )
