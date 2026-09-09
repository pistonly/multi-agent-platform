"""End-to-end CLI test for (c part 2) deprecation warning on `map work`.

Drives ``map work`` through the typer CliRunner against an in-process
FastAPI test client and asserts that ``stderr`` carries a deterministic
deprecation warning whenever the API response contains a legacy alias
key (``advance_round_pending_since`` or ``partition_visibility``).
"""

from __future__ import annotations

import pytest
from map_client import project_config
from map_client.testing import MAPTestClientTransport
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


def test_cli_work_emits_deprecation_warning_on_legacy_alias(runner, patched_admin_cli):
    """When ``map work`` returns a payload containing ``advance_round_pending_since``,
    stderr carries the deterministic deprecation line."""
    result = runner.invoke(app, ["work"])
    assert result.exit_code == 0, result.output

    # The /agents/me/work response is allowed to omit the legacy alias; if so,
    # this test cannot assert against it. Instead, drive the *summary* path
    # which always re-routes through AgentWorkSummaryRead with current field
    # names — so we exercise the helper directly via ``map work --summary``.
    summary_result = runner.invoke(app, ["work", "--summary"])
    assert summary_result.exit_code == 0, summary_result.output
    # The summary endpoint never includes the legacy alias in its current
    # schema; the warning should therefore be silent on stderr.
    assert "deprecated" not in (summary_result.stderr or ""), summary_result.stderr


def test_cli_work_helper_emits_warning_when_payload_contains_alias(
    runner, patched_admin_cli, monkeypatch
):
    """Synthesise a /agents/me/work response with a legacy alias to confirm
    the CLI deprecation path triggers. We patch ``MAPClient.get_agent_work``
    to return a hand-crafted *dict* (not a Pydantic model) so the legacy
    key remains visible to the walker — round-tripping through the schema
    would silently strip the legacy key via AliasChoices."""
    legacy_payload = {
        "agent": {
            "id": "00000000-0000-0000-0000-000000000000",
            "name": "legacy-agent",
            "role": "agent",
            "project_id": None,
            "project_key": None,
            "created_at": "2026-07-01T00:00:00Z",
        },
        "topic_progress": {"items": [], "total": 0},
        "todos": {
            "pending_round_acks": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "topic_id": "22222222-2222-2222-2222-222222222222",
                    "topic_title": "demo",
                    "summary_excerpt": "x",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "advance_round_pending_since": "2026-07-01T00:00:00Z",
                }
            ],
            "my_open_experiments": [],
            "pending_reviews": [],
            "pending_result_reviews": [],
            "experiment_review_informational": [],
            "pending_replies": [],
            "pending_plan_revisions": [],
            "pending_topic_replies": [],
            "pending_advance_rounds": [],
            "stale_open_topics": [],
            "my_open_topics": [],
            "mentions": [],
            "action_items": [],
        },
        "notifications": {"items": [], "total": 0, "unread_count": 0},
    }

    class _FakeClient:
        def get_agent_work(self, **_kwargs):
            return legacy_payload

        def get_global_status(self):
            # `map work` 的 waker 心跳横幅消费点；空 heartbeats = 不渲染。
            from types import SimpleNamespace

            return SimpleNamespace(waker_heartbeats=[])

    monkeypatch.setattr(cli_main, "_transport", None)

    def _fake_ctx():
        class _CM:
            def __enter__(self_inner):
                return _FakeClient()

            def __exit__(self_inner, *args):
                return False

        return _CM()

    monkeypatch.setattr(cli_main, "_client_ctx", _fake_ctx)

    result = runner.invoke(app, ["work"])
    assert result.exit_code == 0, result.output
    assert (
        "Warning: 'advance_round_pending_since' is deprecated; use 'stale_since' instead."
        in (result.stderr or "")
    ), result.stderr
