"""Cursor SDK wake backend for simple-waker.

Local runtime only: the resumed agent must run ``map --persona`` against this
checkout and the gitignored ``.map/`` tokens. Cloud agents clone a fresh repo
and cannot see that state.

``cursor-sdk`` is an optional extra (``pip install -e '.[cursor-runtime]'``)
and is imported lazily so the default Claude waker path stays lightweight.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cli.agent_client import parse_export_env_file
from cli.errors import WorkerError
from cli.session_wake_log import (
    DEFAULT_SESSION_LOG_DIR,
    append_session_event,
    append_session_wake_log,
    resolve_session_log_path,
    text_summary,
)
from cli.wake_backend import WakeResult, clear_runtime_session_state

logger = logging.getLogger("map.cursor_wake_backend")

DEFAULT_CURSOR_MODEL = "composer-2.5"
CURSOR_ENV_FILENAME = ".cursor-env"
CURSOR_ENV_KEYS: frozenset[str] = frozenset({"CURSOR_API_KEY", "CURSOR_MODEL"})
SDK_INSTALL_HINT = "cursor-sdk is not installed. Install with: pip install -e '.[cursor-runtime]'"


def load_project_cursor_env(project_root: Path) -> dict[str, str]:
    """Parse ``.map/.cursor-env`` ``export VAR=...`` lines into {VAR: value}."""
    return parse_export_env_file(Path(project_root) / ".map" / CURSOR_ENV_FILENAME)


def apply_project_cursor_env(project_root: Path) -> dict[str, str]:
    """Make ``.map/.cursor-env`` authoritative for Cursor SDK keys.

    When the file exists, defined keys override inherited ``os.environ`` and
    undeclared Cursor keys are unset so a leftover ``CURSOR_API_KEY`` cannot
    silently win. Missing file leaves the environment unchanged.
    """
    env_path = Path(project_root) / ".map" / CURSOR_ENV_FILENAME
    if not env_path.is_file():
        return {}
    values = load_project_cursor_env(project_root)
    applied: dict[str, str] = {}
    for key in CURSOR_ENV_KEYS:
        if key in values:
            os.environ[key] = values[key]
            applied[key] = values[key]
        elif key in os.environ:
            os.environ.pop(key, None)
    return applied


async def _await_maybe(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class CursorSdkWakeBackend:
    """Long-lived Cursor local-agent backend: one bridge + agent per waker process."""

    def __init__(
        self,
        *,
        project_root: Path,
        persona: str,
        get_agent_state: Callable[[], dict[str, Any]],
        save_state_fn: Callable[[], None],
        model: str | None = None,
        wake_timeout: float = 1800.0,
        session_log_dir: Path | None = None,
        _launch_bridge: Callable[[str], Any] | None = None,
    ) -> None:
        self.project_root = project_root
        self.persona = persona
        self._get_agent_state = get_agent_state
        self._save_state_fn = save_state_fn
        self.model = model
        self.wake_timeout = wake_timeout
        self.session_log_dir = session_log_dir
        self._launch_bridge = _launch_bridge
        self._sdk: Any | None = None
        self._client: Any | None = None
        self._client_cm: Any | None = None
        self._agent: Any | None = None
        self._agent_cm: Any | None = None
        self._connected = False

    async def connect(self) -> None:
        if self._connected and self._agent is not None:
            return
        await self.disconnect()
        os.environ["MAP_RUNTIME_WAKER_PERSONA"] = self.persona
        self._client_cm, self._client = await self._open_bridge()
        resume_id = self._resume_agent_id()
        try:
            self._agent_cm, self._agent = await self._open_agent(resume_id)
        except WorkerError:
            await self.disconnect()
            raise
        except Exception as exc:
            if not resume_id:
                await self.disconnect()
                raise WorkerError(f"Cursor startup failed: {exc}") from exc
            logger.warning(
                "[%s] Cursor resume failed for %s (%s); creating a new agent",
                self.persona,
                resume_id,
                exc,
            )
            state = self._get_agent_state()
            state.pop("cursor_agent_id", None)
            if state.get("runtime_backend") == "cursor":
                state.pop("runtime_session_id", None)
            self._save_state_fn()
            try:
                self._agent_cm, self._agent = await self._open_agent(None)
            except Exception as create_exc:
                await self.disconnect()
                raise WorkerError(f"Cursor startup failed: {create_exc}") from create_exc
        self._connected = True
        self._persist_agent_id()
        logger.info(
            "[%s] Cursor SDK connected (resume=%s)",
            self.persona,
            resume_id or "<new>",
        )

    async def wake_async(
        self,
        *,
        prompt: str,
        event_id: str | None = None,
        event_source: str = "polling",
        fingerprint: str | None = None,
    ) -> WakeResult:
        if not self._connected or self._agent is None:
            await self.connect()
        assert self._agent is not None
        resume_id = self._resume_agent_id() or getattr(self._agent, "agent_id", None)
        pre_sid = str(resume_id or f"new-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}")
        log_path = resolve_session_log_path(self._resolve_session_log_dir(), pre_sid, self.persona)
        self._log_event(
            log_path,
            event="wake",
            summary=text_summary(prompt),
            event_id=event_id,
            event_source=event_source,
            fingerprint=fingerprint,
        )
        run: Any | None = None
        try:
            run = await self._send_prompt(self._agent, prompt)
            result, response_text = await asyncio.wait_for(
                self._collect_run(run, log_path, event_id, event_source, fingerprint),
                timeout=self.wake_timeout,
            )
        except asyncio.TimeoutError as exc:
            self._log_event(
                log_path,
                event="timeout",
                summary=f"no agent result for {self.wake_timeout}s; aborting wake",
                event_id=event_id,
                event_source=event_source,
                fingerprint=fingerprint,
            )
            await self._cancel_run(run)
            await self.disconnect()
            status = "no_response"
            self._append_wake_session_log(
                session_id=pre_sid,
                prompt=prompt,
                response_text="",
                status=status,
                event_id=event_id,
                event_source=event_source,
                fingerprint=fingerprint,
                log_path=log_path,
            )
            raise WorkerError(f"Cursor wake timed out after {self.wake_timeout}s") from exc
        except Exception as exc:
            await self.disconnect()
            raise WorkerError(f"Cursor wake failed: {exc}") from exc

        session_id = self._persist_agent_id() or pre_sid
        status_name = str(getattr(result, "status", "") or "")
        is_error = status_name in {"error", "cancelled", "expired"}
        status = "error" if is_error else "ok"
        if not response_text:
            response_text = str(getattr(result, "result", "") or "")
        self._log_event(
            log_path,
            event="result",
            summary=status,
            event_id=event_id,
            event_source=event_source,
            fingerprint=fingerprint,
        )
        self._append_wake_session_log(
            session_id=str(session_id),
            prompt=prompt,
            response_text=response_text,
            status=status,
            event_id=event_id,
            event_source=event_source,
            fingerprint=fingerprint,
            log_path=log_path,
        )
        if is_error:
            raise WorkerError(f"Cursor wake failed with status={status_name!r}")
        return WakeResult(session_id=str(session_id), response_text=response_text)

    async def disconnect(self) -> None:
        self._connected = False
        agent_cm, agent = self._agent_cm, self._agent
        client_cm, client = self._client_cm, self._client
        self._agent_cm = None
        self._agent = None
        self._client_cm = None
        self._client = None
        await self._close_handle(agent_cm, agent, "agent")
        await self._close_handle(client_cm, client, "client")

    async def reset_session(self) -> None:
        await self.disconnect()
        state = self._get_agent_state()
        clear_runtime_session_state(state)
        self._save_state_fn()

    async def _open_bridge(self) -> tuple[Any | None, Any]:
        workspace = str(self.project_root)
        if self._launch_bridge is not None:
            opened = self._launch_bridge(workspace)
            return await self._enter_async_cm(opened)
        sdk = self._load_sdk()
        opened = sdk.AsyncClient.launch_bridge(
            workspace=workspace,
            local=self._local_options(),
        )
        return await self._enter_async_cm(opened)

    async def _open_agent(self, resume_id: str | None) -> tuple[Any | None, Any]:
        assert self._client is not None
        kwargs = self._agent_kwargs()
        agents = self._client.agents
        opened = self._call_resume(agents, resume_id, kwargs) if resume_id else agents.create(**kwargs)
        return await self._enter_async_cm(opened)

    @staticmethod
    def _call_resume(agents: Any, resume_id: str, kwargs: dict[str, Any]) -> Any:
        try:
            return agents.resume(resume_id, kwargs)
        except TypeError:
            try:
                return agents.resume(resume_id, **kwargs)
            except TypeError:
                return agents.resume(resume_id)

    def _agent_kwargs(self) -> dict[str, Any]:
        return {
            "model": self._resolve_model(),
            "api_key": self._resolve_api_key(),
            "name": f"map-waker-{self.persona}",
            "local": self._local_options(),
        }

    def _local_options(self) -> Any:
        local: Any = {
            "cwd": str(self.project_root),
            "setting_sources": ["project"],
        }
        if self._sdk is not None:
            local_cls = getattr(self._sdk, "LocalAgentOptions", None)
            if local_cls is not None:
                return local_cls(
                    cwd=str(self.project_root),
                    setting_sources=["project"],
                )
        return local

    async def _send_prompt(self, agent: Any, prompt: str) -> Any:
        send_options = self._make_send_options()
        if send_options is not None:
            try:
                return await _await_maybe(agent.send(prompt, send_options))
            except TypeError:
                pass
        return await _await_maybe(agent.send(prompt))

    def _make_send_options(self) -> Any | None:
        if self._sdk is None:
            return None
        send_cls = getattr(self._sdk, "SendOptions", None)
        if send_cls is None:
            return None
        try:
            return send_cls(local={"force": True})
        except TypeError:
            return None

    async def _collect_run(
        self,
        run: Any,
        log_path: Path,
        event_id: str | None,
        event_source: str,
        fingerprint: str | None,
    ) -> tuple[Any, str]:
        text_parts: list[str] = []
        messages_fn = getattr(run, "messages", None)
        if callable(messages_fn):
            stream = messages_fn()
            if inspect.isawaitable(stream):
                stream = await stream
            if hasattr(stream, "__aiter__"):
                async for message in stream:
                    text_parts.extend(
                        self._consume_message(
                            message,
                            log_path,
                            event_id,
                            event_source,
                            fingerprint,
                        )
                    )
            elif hasattr(stream, "__iter__") and not isinstance(stream, str | bytes):
                for message in stream:
                    text_parts.extend(
                        self._consume_message(
                            message,
                            log_path,
                            event_id,
                            event_source,
                            fingerprint,
                        )
                    )
        wait_fn = getattr(run, "wait", None)
        if not callable(wait_fn):
            raise WorkerError("Cursor run does not support wait()")
        result = await _await_maybe(wait_fn())
        return result, "".join(text_parts)

    def _consume_message(
        self,
        message: Any,
        log_path: Path,
        event_id: str | None,
        event_source: str,
        fingerprint: str | None,
    ) -> list[str]:
        msg_type = getattr(message, "type", None)
        if msg_type != "assistant":
            return []
        inner = getattr(message, "message", message)
        content = getattr(inner, "content", None)
        parts: list[str] = []
        blocks: list[Any]
        if isinstance(content, str):
            blocks = [SimpleNamespace(type="text", text=content)]
        elif isinstance(content, list):
            blocks = content
        else:
            return []
        for block in blocks:
            if getattr(block, "type", None) != "text":
                continue
            text = str(getattr(block, "text", "") or "")
            if not text:
                continue
            parts.append(text)
            self._log_event(
                log_path,
                event="text",
                summary=text_summary(text),
                event_id=event_id,
                event_source=event_source,
                fingerprint=fingerprint,
            )
        return parts

    async def _cancel_run(self, run: Any | None) -> None:
        if run is None:
            return
        supports = getattr(run, "supports", None)
        if callable(supports):
            try:
                if not supports("cancel"):
                    return
            except Exception:  # noqa: BLE001
                return
        cancel = getattr(run, "cancel", None)
        if not callable(cancel):
            return
        try:
            await _await_maybe(cancel())
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Cursor run cancel failed: %s", self.persona, exc)

    async def _enter_async_cm(self, value: Any) -> tuple[Any | None, Any]:
        value = await _await_maybe(value)
        enter = getattr(value, "__aenter__", None)
        if callable(enter):
            entered = await _await_maybe(enter())
            return value, entered
        return None, value

    async def _close_handle(self, cm: Any | None, obj: Any | None, label: str) -> None:
        if cm is not None:
            exit_fn = getattr(cm, "__aexit__", None)
            if callable(exit_fn):
                try:
                    await _await_maybe(exit_fn(None, None, None))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Cursor %s close failed: %s", self.persona, label, exc)
                return
        if obj is None:
            return
        for method_name in ("aclose", "close"):
            close_fn = getattr(obj, method_name, None)
            if not callable(close_fn):
                continue
            try:
                await _await_maybe(close_fn())
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Cursor %s %s failed: %s", self.persona, label, method_name, exc)
            return

    def _load_sdk(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        try:
            from cursor_sdk import AsyncClient, CursorAgentError, LocalAgentOptions
        except ImportError as exc:
            raise WorkerError(SDK_INSTALL_HINT) from exc
        send_options = None
        try:
            from cursor_sdk import SendOptions as _SendOptions
        except ImportError:
            _SendOptions = None  # type: ignore[misc, assignment]
        else:
            send_options = _SendOptions
        self._sdk = SimpleNamespace(
            AsyncClient=AsyncClient,
            LocalAgentOptions=LocalAgentOptions,
            CursorAgentError=CursorAgentError,
            SendOptions=send_options,
        )
        return self._sdk

    def _resume_agent_id(self) -> str | None:
        state = self._get_agent_state()
        backend = state.get("runtime_backend")
        if backend not in (None, "cursor"):
            return None
        agent_id = state.get("cursor_agent_id")
        if agent_id:
            return str(agent_id)
        if backend == "cursor":
            session_id = state.get("runtime_session_id")
            if session_id:
                return str(session_id)
        return None

    def _persist_agent_id(self) -> str | None:
        agent_id = getattr(self._agent, "agent_id", None)
        state = self._get_agent_state()
        state["runtime_backend"] = "cursor"
        if agent_id:
            sid = str(agent_id)
            state["cursor_agent_id"] = sid
            state["runtime_session_id"] = sid
            self._save_state_fn()
            return sid
        self._save_state_fn()
        return state.get("cursor_agent_id") or state.get("runtime_session_id")

    def _resolve_api_key(self) -> str:
        key = (os.environ.get("CURSOR_API_KEY") or "").strip()
        if key:
            return key
        key = (load_project_cursor_env(self.project_root).get("CURSOR_API_KEY") or "").strip()
        if key:
            return key
        raise WorkerError(
            "CURSOR_API_KEY not found. Set it or add `export CURSOR_API_KEY=...` to .map/.cursor-env"
        )

    def _resolve_model(self) -> str:
        if self.model and self.model.strip():
            return self.model.strip()
        env_model = (os.environ.get("CURSOR_MODEL") or "").strip()
        if env_model:
            return env_model
        file_model = (load_project_cursor_env(self.project_root).get("CURSOR_MODEL") or "").strip()
        if file_model:
            return file_model
        return DEFAULT_CURSOR_MODEL

    def _resolve_session_log_dir(self) -> Path:
        if self.session_log_dir is not None:
            return self.session_log_dir
        override = os.environ.get("MAP_SESSION_WAKE_LOG_DIR", "").strip()
        if override:
            return Path(override)
        return self.project_root / DEFAULT_SESSION_LOG_DIR

    def _session_log_disabled(self) -> bool:
        return os.environ.get("MAP_SESSION_WAKE_LOG", "1").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
        }

    def _log_event(
        self,
        log_path: Path,
        *,
        event: str,
        summary: str,
        event_id: str | None = None,
        event_source: str = "polling",
        fingerprint: str | None = None,
    ) -> None:
        if self._session_log_disabled():
            return
        try:
            append_session_event(
                log_path=log_path,
                persona=self.persona,
                integration="waker",
                event=event,
                summary=summary,
                event_id=event_id,
                event_source=event_source,
                fingerprint=fingerprint,
            )
        except OSError as exc:
            logger.warning("[%s] session event log write failed: %s", self.persona, exc)

    def _append_wake_session_log(
        self,
        *,
        session_id: str,
        prompt: str,
        response_text: str,
        status: str,
        event_id: str | None = None,
        event_source: str = "polling",
        fingerprint: str | None = None,
        log_path: Path | None = None,
    ) -> None:
        if self._session_log_disabled():
            return
        try:
            append_session_wake_log(
                log_dir=self._resolve_session_log_dir(),
                session_id=session_id,
                persona=self.persona,
                integration="waker",
                prompt=prompt,
                response_text=response_text,
                status=status,
                event_id=event_id,
                event_source=event_source,
                fingerprint=fingerprint,
                log_path=log_path,
            )
        except OSError as exc:
            logger.warning("[%s] session wake log write failed: %s", self.persona, exc)


__all__ = [
    "CURSOR_ENV_KEYS",
    "DEFAULT_CURSOR_MODEL",
    "CursorSdkWakeBackend",
    "SDK_INSTALL_HINT",
    "apply_project_cursor_env",
    "load_project_cursor_env",
]
