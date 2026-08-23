"""Tests for cli/orchestrator.py — Host-orchestrated agent invocation.

Tests cover:
- HostOrchestrator client creation and caching
- invoke() with mocked PersonaAgentClient
- run_invoke() synchronous wrapper
- CLI command `map host invoke` parameter parsing
- Error handling (missing prompt, waker running)

host invoke 可观测性 v1 (5a50c841): A1 timeout / A2 follow / A3 session-state
lines are covered in TestHostOrchestrator and TestHostInvokeCommand below.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from cli.commands.host import host_app
from cli.orchestrator import InvokeResult

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

    # --- 5a50c841 A3: session-state line ---

    @pytest.mark.asyncio
    async def test_invoke_session_state_running_when_resuming(self, tmp_path):
        """A3: resuming an existing session reports session_state='running'."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client()
        mock_client.state = {"claude_session_id": "existing-session-9"}

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        result = await orchestrator.invoke("participant", "hello")

        assert result.session_state == "running"

    @pytest.mark.asyncio
    async def test_invoke_session_state_waiting_for_new_session(self, tmp_path):
        """A3: no session id means a new session is about to start."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client()
        mock_client.state = {}

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        result = await orchestrator.invoke("participant", "hello")

        assert result.session_state == "waiting-for-session"

    # --- 5a50c841 A1: timeout ---

    @pytest.mark.asyncio
    async def test_invoke_timeout_returns_friendly_timeout(self, tmp_path):
        """A1: a hung wake_up should return status='timeout' with metadata."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(30)

        mock_client.wake_up = AsyncMock(side_effect=_hang)

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        result = await orchestrator.invoke("participant", "slow task", timeout=0.05)

        assert result.status == "timeout"
        assert result.timed_out is True
        assert result.waited_seconds == 0.05
        assert result.session_state in ("waiting-for-session", "running")
        # friendly message includes the wait ceiling and session state, no stack
        assert "exceeded 0.05s and was cancelled" in (result.error or "")
        assert "target session state=" in (result.error or "")

    # --- 5a50c841 A2: --follow streaming ---

    @pytest.mark.asyncio
    async def test_invoke_follow_forwards_events_to_stream(self, tmp_path):
        """A2: follow=True should forward streamed events to on_stream."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client(
            response_text="streamed text",
            session_id="sess-1",
        )

        streamed: list[dict[str, Any]] = []
        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        result = await orchestrator.invoke(
            "participant",
            "task",
            follow=True,
            on_stream=streamed.append,
        )

        assert result.status == "ok"
        event_types = [e["type"] for e in streamed]
        assert "text" in event_types
        assert "result" in event_types
        assert any(e.get("content") == "streamed text" for e in streamed)

    @pytest.mark.asyncio
    async def test_invoke_no_follow_does_not_stream(self, tmp_path):
        """A2: without follow, on_stream should not be called."""
        from cli.orchestrator import HostOrchestrator

        mock_client = self._make_mock_client(response_text="x", session_id="s2")
        on_stream = MagicMock()

        orchestrator = HostOrchestrator(project_root=tmp_path, ignore_waker=True)
        orchestrator._get_or_create_client = MagicMock(return_value=mock_client)

        await orchestrator.invoke("participant", "task", follow=False, on_stream=on_stream)

        on_stream.assert_not_called()


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

    def test_run_invoke_forwards_timeout_and_stream(self, tmp_path):
        """run_invoke should forward timeout / follow / on_stream kwargs."""
        from cli.orchestrator import run_invoke

        on_stream = MagicMock()
        with patch("cli.orchestrator.HostOrchestrator") as mock_cls:
            mock_orch = MagicMock()
            mock_orch.invoke = AsyncMock(
                return_value=InvokeResult(
                    persona="participant",
                    status="ok",
                    response_text="ok",
                    session_state="waiting-for-session",
                )
            )
            mock_orch.disconnect_all = AsyncMock()
            mock_cls.return_value = mock_orch

            run_invoke(
                persona="participant",
                prompt="p",
                project_root=tmp_path,
                timeout=7.5,
                follow=True,
                on_stream=on_stream,
            )

        invoke_kwargs = mock_orch.invoke.call_args.kwargs
        assert invoke_kwargs["timeout"] == 7.5
        assert invoke_kwargs["follow"] is True
        assert invoke_kwargs["on_stream"] is on_stream


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

    def test_timeout_and_follow_forwarded_to_run_invoke(self):
        """--timeout/--follow should forward to run_invoke with a stream cb."""
        with patch("cli.orchestrator.run_invoke") as mock_run:
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
                    "--timeout", "5",
                    "--follow",
                ],
            )

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["timeout"] == 5.0
        assert call_kwargs["follow"] is True
        assert callable(call_kwargs["on_stream"])

    def test_timeout_exits_nonzero_and_dispatches_cancel(self):
        """A1: timed_out result should exit 1, print friendly error, dispatch."""
        with (
            patch("cli.orchestrator.run_invoke") as mock_run,
            patch("cli.commands.host._dispatch_cancel_notification") as mock_dispatch,
        ):
            mock_run.return_value = InvokeResult(
                persona="participant",
                status="timeout",
                response_text="",
                timed_out=True,
                waited_seconds=5.0,
                session_state="waiting-for-session",
                error=(
                    "waiting for 'participant' exceeded 5s and was cancelled; "
                    "target session state=waiting-for-session"
                ),
            )

            result = runner.invoke(
                host_app,
                ["invoke", "--persona", "participant", "--prompt", "t", "--timeout", "5"],
            )

        assert result.exit_code == 1
        assert "cancelled" in (result.stderr or "")
        assert mock_dispatch.call_count == 1

    def test_dispatch_cancel_notification_sends_wakeable(self, tmp_path, monkeypatch):
        """A1: cancel notification should be a wakeable dispatch to target."""
        from types import SimpleNamespace

        from cli.commands.host import _dispatch_cancel_notification

        sent: dict[str, Any] = {}

        class _FakeClient:
            def get_me(self):
                return SimpleNamespace(id="me-id", project_id="proj-id")

            def list_agents(self, *, project_id=None):
                return [
                    SimpleNamespace(
                        id="part-id",
                        name="multi-agents-platform-participant",
                    )
                ]

            def dispatch_notification(self, **kwargs):
                sent.update(kwargs)

        fake = _FakeClient()
        monkeypatch.setattr("map_client.project_config.resolve_client", lambda **kw: fake)

        result = InvokeResult(
            persona="participant",
            status="timeout",
            timed_out=True,
            waited_seconds=4.0,
            session_state="waiting-for-session",
        )
        _dispatch_cancel_notification("participant", result, tmp_path)

        assert sent["recipient_agent_id"] == "part-id"
        assert sent["event"] == "host.invoke.cancelled"
        assert sent["wakeable"] is True
        assert "4s" in sent["summary"] or "4" in sent["summary"]
        assert "waiting-for-session" in sent["summary"]


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
