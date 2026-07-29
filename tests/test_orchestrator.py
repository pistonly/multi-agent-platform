"""Tests for cli/orchestrator.py — Host-orchestrated agent invocation.

Tests cover:
- HostOrchestrator client creation and caching
- invoke() with mocked PersonaAgentClient
- run_invoke() synchronous wrapper
- CLI command `map host invoke` parameter parsing
- Error handling (missing prompt, waker running)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from cli.commands.host import host_app


runner = CliRunner()


# ---------------------------------------------------------------------------
# HostOrchestrator unit tests (mocked PersonaAgentClient)
# ---------------------------------------------------------------------------


class TestHostOrchestrator:
    """Unit tests for HostOrchestrator with mocked Claude SDK."""

    def _make_mock_client(
        self,
        *,
        response_text: str = "mock response",
        status: str = "ok",
        session_id: str | None = "test-session-123",
    ) -> MagicMock:
        """Create a mock PersonaAgentClient."""
        client = MagicMock()
        client._connected = False
        client.state: dict[str, Any] = {}
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.wake_up = AsyncMock(return_value=status)

        # wake_up calls on_event with text and result events
        def _wake_up_side_effect(prompt, *, on_event=None, **kwargs):
            if on_event is not None:
                if response_text:
                    on_event({"type": "text", "content": response_text})
                if session_id:
                    on_event({"type": "result", "session_id": session_id})
            return status

        client.wake_up.side_effect = _wake_up_side_effect
        return client

    @pytest.mark.asyncio
    async def test_invoke_returns_response_text(self, tmp_path):
        """invoke() should return the response text from the persona agent."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client(response_text="I agree with the plan")

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        result = await orchestrator.invoke("participant", "comment on topic X")

        assert result.persona == "participant"
        assert result.status == "ok"
        assert result.response_text == "I agree with the plan"
        assert result.session_id == "test-session-123"

    @pytest.mark.asyncio
    async def test_invoke_calls_connect_and_wake_up(self, tmp_path):
        """invoke() should call connect() then wake_up() on the client."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client()

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        await orchestrator.invoke("reviewer", "review experiment Y")

        mock_client.connect.assert_awaited_once()
        mock_client.wake_up.assert_awaited_once()
        # Verify event_source is "orchestrator"
        call_kwargs = mock_client.wake_up.call_args
        assert call_kwargs.kwargs.get("event_source") == "orchestrator"

    @pytest.mark.asyncio
    async def test_invoke_new_session_clears_state(self, tmp_path):
        """invoke(new_session=True) should clear session IDs."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client()
        mock_client.state = {
            "claude_session_id": "old-session",
            "runtime_session_id": "old-session",
        }

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        await orchestrator.invoke("participant", "test", new_session=True)

        assert "claude_session_id" not in mock_client.state
        assert "runtime_session_id" not in mock_client.state

    @pytest.mark.asyncio
    async def test_invoke_error_status(self, tmp_path):
        """invoke() should return status='error' when agent fails."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client(
            response_text="",
            status="error",
            session_id=None,
        )

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        result = await orchestrator.invoke("participant", "failing task")

        assert result.status == "error"
        assert result.response_text == ""

    @pytest.mark.asyncio
    async def test_disconnect_all_disconnects_clients(self, tmp_path):
        """disconnect_all() should disconnect all managed clients."""
        from cli.orchestrator import HostOrchestrator

        mock_client1 = self._make_mock_client()
        mock_client2 = self._make_mock_client()

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._clients = {
            "participant": mock_client1,
            "reviewer": mock_client2,
        }

        await orchestrator.disconnect_all()

        mock_client1.disconnect.assert_awaited_once()
        mock_client2.disconnect.assert_awaited_once()
        assert len(orchestrator._clients) == 0

    @pytest.mark.asyncio
    async def test_client_caching(self, tmp_path):
        """_get_or_create_client should cache clients per persona."""
        from cli.orchestrator import HostOrchestrator

        mock_participant = self._make_mock_client(response_text="participant")
        mock_reviewer = self._make_mock_client(response_text="reviewer")

        with (
            patch("cli.orchestrator.load_bridge_state", return_value={"personas": {}}),
            patch("cli.orchestrator.save_bridge_state"),
            patch("cli.orchestrator.sync_runtime_skills"),
            patch("cli.orchestrator.PersonaAgentClient") as mock_client_cls,
        ):
            mock_client_cls.side_effect = [mock_participant, mock_reviewer]

            orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
            client1 = orchestrator._get_or_create_client("participant")
            client2 = orchestrator._get_or_create_client("participant")

            assert client1 is client2
            assert mock_client_cls.call_count == 1

            # Different persona creates a new client
            client3 = orchestrator._get_or_create_client("reviewer")
            assert client3 is not client1
            assert mock_client_cls.call_count == 2


# ---------------------------------------------------------------------------
# run_invoke synchronous wrapper tests
# ---------------------------------------------------------------------------


class TestRunInvoke:
    """Tests for the run_invoke() synchronous wrapper."""

    def test_run_invoke_returns_result(self, tmp_path):
        """run_invoke should return an InvokeResult."""
        from cli.orchestrator import InvokeResult, run_invoke

        mock_client = MagicMock()
        mock_client._connected = False
        mock_client.state = {}
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.wake_up = AsyncMock(return_value="ok")

        def _side_effect(prompt, *, on_event=None, **kwargs):
            if on_event:
                on_event({"type": "text", "content": "sync response"})
            return "ok"

        mock_client.wake_up.side_effect = _side_effect

        with patch("cli.orchestrator.HostOrchestrator") as mock_cls:
            mock_orch = MagicMock()
            mock_orch.invoke = AsyncMock(
                return_value=InvokeResult(
                    persona="participant",
                    status="ok",
                    response_text="sync response",
                    session_id="sid-1",
                )
            )
            mock_orch.disconnect_all = AsyncMock()
            mock_cls.return_value = mock_orch

            result = run_invoke(
                persona="participant",
                prompt="test prompt",
                project_root=tmp_path,
                ignore_waker=True,
            )

        assert result.persona == "participant"
        assert result.status == "ok"
        assert result.response_text == "sync response"
        assert result.session_id == "sid-1"


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestHostInvokeCommand:
    """Tests for `map host invoke` CLI command."""

    def test_missing_prompt_and_prompt_file_errors(self):
        """Both --prompt and --prompt-file missing should error."""
        result = runner.invoke(
            host_app,
            ["invoke", "--persona", "participant"],
        )
        assert result.exit_code == 1
        assert "either --prompt or --prompt-file is required" in (result.stdout + result.output)

    def test_prompt_file_overrides_prompt(self, tmp_path):
        """--prompt-file should take precedence over --prompt."""
        prompt_file = tmp_path / "task.md"
        prompt_file.write_text("prompt from file", encoding="utf-8")

        with patch("cli.orchestrator.run_invoke") as mock_run:
            from cli.orchestrator import InvokeResult

            mock_run.return_value = InvokeResult(
                persona="participant",
                status="ok",
                response_text="file response",
            )

            result = runner.invoke(
                host_app,
                [
                    "invoke",
                    "--persona", "participant",
                    "--prompt", "inline prompt",
                    "--prompt-file", str(prompt_file),
                ],
            )

        assert result.exit_code == 0
        # Verify prompt_file content was used
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["prompt"] == "prompt from file"

    def test_json_output_format(self):
        """--json should output JSON with response and metadata."""
        with patch("cli.orchestrator.run_invoke") as mock_run:
            from cli.orchestrator import InvokeResult

            mock_run.return_value = InvokeResult(
                persona="reviewer",
                status="ok",
                response_text="review passed",
                session_id="sess-abc",
            )

            result = runner.invoke(
                host_app,
                [
                    "invoke",
                    "--persona", "reviewer",
                    "--prompt", "review this",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        import json as json_lib
        data = json_lib.loads(result.stdout)
        assert data["persona"] == "reviewer"
        assert data["status"] == "ok"
        assert data["response"] == "review passed"
        assert data["session_id"] == "sess-abc"

    def test_error_status_exits_nonzero(self):
        """Error status should exit with code 1."""
        with patch("cli.orchestrator.run_invoke") as mock_run:
            from cli.orchestrator import InvokeResult

            mock_run.return_value = InvokeResult(
                persona="participant",
                status="error",
                response_text="",
            )

            result = runner.invoke(
                host_app,
                [
                    "invoke",
                    "--persona", "participant",
                    "--prompt", "fail task",
                ],
            )

        assert result.exit_code == 1

    def test_text_output_default(self):
        """Default output should be plain text response."""
        with patch("cli.orchestrator.run_invoke") as mock_run:
            from cli.orchestrator import InvokeResult

            mock_run.return_value = InvokeResult(
                persona="participant",
                status="ok",
                response_text="Here is my comment on the topic.",
                session_id="s-1",
            )

            result = runner.invoke(
                host_app,
                [
                    "invoke",
                    "--persona", "participant",
                    "--prompt", "comment on topic",
                ],
            )

        assert result.exit_code == 0
        assert "Here is my comment on the topic." in result.stdout

    def test_passes_options_to_run_invoke(self):
        """CLI options should be forwarded to run_invoke."""
        with patch("cli.orchestrator.run_invoke") as mock_run:
            from cli.orchestrator import InvokeResult

            mock_run.return_value = InvokeResult(
                persona="participant",
                status="ok",
                response_text="ok",
            )

            runner.invoke(
                host_app,
                [
                    "invoke",
                    "--persona", "participant",
                    "--prompt", "test",
                    "--new-session",
                    "--model", "claude-sonnet-4-20250514",
                    "--ignore-waker",
                ],
            )

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["persona"] == "participant"
        assert call_kwargs["prompt"] == "test"
        assert call_kwargs["new_session"] is True
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["ignore_waker"] is True


# ---------------------------------------------------------------------------
# Integration: CLI registration in main app
# ---------------------------------------------------------------------------


class TestHostAppRegistration:
    """Verify `host` sub-app is registered in the main CLI."""

    def test_host_command_exists_in_main_app(self):
        """`map host --help` should list the host sub-app."""
        from cli.main import app

        result = runner.invoke(app, ["host", "--help"])
        assert result.exit_code == 0
        assert "invoke" in result.stdout.lower()

    def test_host_invoke_help(self):
        """`map host invoke --help` should show help text."""
        result = runner.invoke(host_app, ["invoke", "--help"])
        assert result.exit_code == 0
        assert "invoke" in result.stdout.lower() or "Invoke" in result.stdout
