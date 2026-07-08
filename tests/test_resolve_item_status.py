"""``map experiment review resolve-item --status`` flag (experiment b95894db I1(a)).

Pins the **CLI contract** for plan (a):

1. Omitting ``--status`` defaults to ``resolved`` — the legacy behaviour.
2. ``--status resolved`` resolves the item.
3. ``--status rebutted`` flips the item to ``rebutted`` (host pushes back
   while keeping the experiment moving).
4. ``--help`` surfaces the new ``--status`` flag.

The CLI contract is asserted by monkey-patching
``MAPClient.update_review_item`` and capturing the ``ReviewItemStatus`` enum
that the CLI actually passes through. The state-machine end-to-end coverage
for ``open → rebutted`` lives in plan (c) (review_item.state migration); this
file intentionally scopes itself to the parameter plumbing so the (a)
acceptance criterion can land independently of (c).
"""

from __future__ import annotations

import pytest
from map_client import project_config
from map_client.testing import MAPTestClientTransport
from map_types.enums import ReviewItemStatus
from map_types.schemas import ReviewItemRead
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

pytestmark = pytest.mark.slow


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_admin_cli(monkeypatch, client, admin_headers):
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )


def _stub_update_review_item(monkeypatch, captured: list):
    """Replace ``MAPClient.update_review_item`` with a recording stub.

    Returns a fake review item so the CLI's YAML output stays well-formed.
    """
    def _fake(self, item_id, status):
        captured.append({"item_id": item_id, "status": status})
        return ReviewItemRead.model_validate(
            {
                "id": str(item_id),
                "review_id": "00000000-0000-0000-0000-000000000000",
                "kind": "unreasonable",
                "content": "stub",
                "status": status.value,
                "created_at": "2026-07-08T00:00:00Z",
                "updated_at": "2026-07-08T00:00:00Z",
            }
        )

    monkeypatch.setattr("map_client.client.MAPClient.update_review_item", _fake)


def test_resolve_item_default_status_is_resolved_for_backward_compat(
    runner, patched_admin_cli, monkeypatch
):
    """Omitting ``--status`` must default to ``resolved`` (legacy behaviour)."""
    captured: list = []
    _stub_update_review_item(monkeypatch, captured)

    result = runner.invoke(
        app,
        [
            "experiment",
            "review",
            "resolve-item",
            "--id",
            "11111111-1111-1111-1111-111111111111",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured == [
        {
            "item_id": _to_uuid("11111111-1111-1111-1111-111111111111"),
            "status": ReviewItemStatus.resolved,
        }
    ]


def test_resolve_item_explicit_status_resolved(
    runner, patched_admin_cli, monkeypatch
):
    """``--status resolved`` resolves the item."""
    captured: list = []
    _stub_update_review_item(monkeypatch, captured)

    result = runner.invoke(
        app,
        [
            "experiment",
            "review",
            "resolve-item",
            "--id",
            "22222222-2222-2222-2222-222222222222",
            "--status",
            "resolved",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured[0]["status"] == ReviewItemStatus.resolved


def test_resolve_item_explicit_status_rebutted(
    runner, patched_admin_cli, monkeypatch
):
    """``--status rebutted`` flips the item to ``rebutted`` (host push-back)."""
    captured: list = []
    _stub_update_review_item(monkeypatch, captured)

    result = runner.invoke(
        app,
        [
            "experiment",
            "review",
            "resolve-item",
            "--id",
            "33333333-3333-3333-3333-333333333333",
            "--status",
            "rebutted",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured[0]["status"] == ReviewItemStatus.rebutted


def test_resolve_item_rejects_unknown_status_string(
    runner, patched_admin_cli, monkeypatch
):
    """An unknown ``--status`` value must surface as a clean error, not a
    silent fallback to ``resolved``."""
    captured: list = []
    _stub_update_review_item(monkeypatch, captured)

    result = runner.invoke(
        app,
        [
            "experiment",
            "review",
            "resolve-item",
            "--id",
            "44444444-4444-4444-4444-444444444444",
            "--status",
            "totally-not-a-real-status",
        ],
    )
    assert result.exit_code != 0
    assert captured == [], "update_review_item must NOT be called for unknown status"
    assert "totally-not-a-real-status" in (result.output or "")


def test_resolve_item_help_documents_status_option(runner, patched_admin_cli):
    """``--help`` must surface the new ``--status`` flag so discovery works."""
    result = runner.invoke(
        app, ["experiment", "review", "resolve-item", "--help"]
    )
    assert result.exit_code == 0, result.output
    assert "--status" in result.output
    assert "resolved" in result.output
    assert "rebutted" in result.output


def _to_uuid(s: str):
    import uuid

    return uuid.UUID(s)
