"""In-process MAP client with the waker/e2e-facing MapCommandClient surface (T24).

``simple-waker`` used to pay a full ``map`` subprocess (Typer + pydantic cold
start) on every ``work`` / ``whoami`` / mark-* call. This adapter talks to
``MAPClient`` in-process, dumps pydantic models to JSON-shaped dicts so the
waker's existing dict parsing stays unchanged, and maps SDK errors to
``WorkerError``.

The e2e driver (:mod:`cli.e2e_collab`) migrated here in T24 2/2 — its
read-only state queries (``topic_show`` / ``experiment_list`` /
``experiment_status``) reuse the same FS merge helpers as the CLI commands,
so local ``map/`` folders stay the display authority without a subprocess.
``MapCommandClient`` is deprecated and remains only as the waker's
``--subprocess-client`` rollback path.
Rollback: ``map-simple-waker --subprocess-client`` or ``MAP_WAKER_SUBPROCESS=1``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
import typer
from map_client.exceptions import MAPConflictError, MAPError
from map_client.project_config import ProjectMapConfig, load_project_map_config
from map_types.enums import InboundEventSource, TopicStatus
from map_types.schemas.agent import AgentHeartbeatCreate, AgentHeartbeatResult
from map_types.schemas.inbound_event import InboundEventCreate

from cli.errors import WorkerError

T = TypeVar("T")


def _dump(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, list):
        return [_dump(item) for item in value]
    return value


class MapSdkClient:
    """Waker client backed by in-process ``MAPClient`` (no ``map`` subprocess)."""

    def __init__(
        self,
        *,
        persona: str = "host",
        project_root: Path | None = None,
        dry_run: bool = False,
        sdk: Any = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.persona = persona
        self.project_root = project_root
        self.dry_run = dry_run
        self._sdk = sdk
        self._owns_sdk = sdk is None
        self._transport = transport
        self._cfg: ProjectMapConfig | None = None

    def close(self) -> None:
        if self._owns_sdk and self._sdk is not None:
            closer = getattr(self._sdk, "close", None)
            if callable(closer):
                closer()
            self._sdk = None

    def _client(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        try:
            self._cfg = load_project_map_config(project_root=self.project_root)
        except ValueError as exc:
            raise WorkerError(str(exc)) from exc
        self._sdk = self._cfg.client_for(self.persona, transport=self._transport)
        return self._sdk

    def _call(self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except MAPError as exc:
            raise WorkerError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise WorkerError(str(exc)) from exc

    def _project_id(self) -> uuid.UUID:
        cfg = self._cfg
        if cfg is None:
            self._client()
            cfg = self._cfg
        if cfg is not None and cfg.project_id:
            return uuid.UUID(str(cfg.project_id))
        project_key = cfg.project_key if cfg is not None else None
        if not project_key:
            raise WorkerError("project_id / project_key missing in .map/config.yaml")
        project = self._call(lambda: self._client().get_project_by_key(project_key))
        return project.id

    def whoami(self) -> dict[str, Any]:
        return _dump(self._call(lambda: self._client().get_me()))

    def work(self) -> dict[str, Any]:
        return _dump(
            self._call(
                lambda: self._client().get_agent_work(
                    notification_category="wakeable",
                    client="waker",
                )
            )
        )

    def topic_list_open(self) -> list[dict[str, Any]]:
        project_id = self._project_id()
        page = 1
        page_size = 100
        all_rows: list[dict[str, Any]] = []
        while True:
            rows = self._call(
                lambda p=page: self._client().list_topics(
                    project_id,
                    status=TopicStatus.open,
                    page=p,
                    page_size=page_size,
                )
            )
            dumped = [_dump(row) for row in rows]
            all_rows.extend(r for r in dumped if isinstance(r, dict))
            if len(dumped) < page_size:
                break
            page += 1
        return all_rows

    # --- e2e driver read queries (T24 2/2) ------------------------------------

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        """GET ``/topics/{id}`` — server 端已合并 FS uuid5 与 DB 话题。"""
        return _dump(self._call(lambda: self._client().get_topic(uuid.UUID(str(topic_id)))))

    def experiment_list(self, *, phase: str | None = None) -> list[dict[str, Any]]:
        """与 CLI ``experiment list`` 同源的合并视图（本地 map/experiments 显示权威）。

        lazy import 复用 ``cli.commands.experiment`` / ``cli.experiment_fs``
        的合并编排，waker 热路径不拖入 commands 模块。
        """
        from map_types.enums import ExperimentPhase

        from cli.commands.experiment import _list_api_experiments_all
        from cli.experiment_fs import (
            filter_experiment_summaries,
            iter_indexed_experiments,
            merge_experiment_summaries,
            should_scan_local_experiments,
            workspace_root,
        )

        pid = self._project_id()
        phase_filter = ExperimentPhase(phase) if phase else None
        if should_scan_local_experiments(None, None, pid):
            workspace = workspace_root()
            if workspace is not None:
                api_items = self._call(
                    lambda: _list_api_experiments_all(self._client(), pid)
                )
                merged = merge_experiment_summaries(
                    iter_indexed_experiments(workspace), api_items, pid, workspace
                )
                rows = filter_experiment_summaries(merged, phase=phase_filter)
                return [_dump(row) for row in rows]
        rows = self._call(
            lambda: self._client().list_experiments(pid, phase=phase_filter)
        )
        return [_dump(row) for row in rows]

    def experiment_status(self, experiment_id: str) -> dict[str, Any]:
        """与 CLI ``experiment status`` 同源：DB GET + FS overlay，404 时从 index.md 合成。"""
        from cli.commands.experiment import _load_experiment

        return _dump(self._call(lambda: _load_experiment(self._client(), experiment_id)))

    def experiment_scan_stalled_locks(self) -> dict[str, Any] | None:
        if self.dry_run:
            typer.echo("[dry-run] experiment lock scan-stalled")
            return None
        return _dump(self._call(lambda: self._client().scan_stalled_experiment_locks()))

    def action_mark_wake_sent(self, action_item_id: str) -> dict[str, Any] | None:
        if self.dry_run:
            typer.echo(f"[dry-run] action mark-wake-sent --id {action_item_id}")
            return None
        item_uuid = uuid.UUID(str(action_item_id))
        return _dump(self._call(lambda: self._client().mark_wake_sent(item_uuid)))

    def action_mark_stale(self, action_item_id: str) -> dict[str, Any] | None:
        if self.dry_run:
            typer.echo(f"[dry-run] action mark-stale --id {action_item_id}")
            return None
        item_uuid = uuid.UUID(str(action_item_id))
        return _dump(self._call(lambda: self._client().mark_stale(item_uuid)))

    def inbound_event_record(
        self,
        *,
        event_id: str,
        fingerprint: str,
        event_type: str,
        source: str = "polling",
    ) -> bool:
        if self.dry_run:
            typer.echo("[dry-run] inbound-event record")
            return True
        payload = InboundEventCreate(
            event_id=uuid.UUID(str(event_id)),
            fingerprint=fingerprint,
            event_type=event_type,
            source=InboundEventSource(source),
        )
        try:
            result = self._client().record_inbound_event(payload)
        except MAPConflictError:
            return False
        except MAPError as exc:
            raise WorkerError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise WorkerError(str(exc)) from exc
        status = getattr(result, "status", None)
        return status not in {"duplicate", "rejected_v1"}

    def agent_heartbeat(
        self,
        *,
        busy_since: datetime | None,
    ) -> AgentHeartbeatResult | None:
        """PATCH ``agents.last_busy_since``（实验 b3ec2e4d I2 — A1 验收）。

        ``busy_since=None`` 视为 idle 清零。waker state machine 在
        ``wake_async`` 前调用，``finally`` 清零。失败抛 ``WorkerError``，
        由 simple-waker 兜底（不阻塞 remind 主流程）。
        """
        if self.dry_run:
            typer.echo(f"[dry-run] agent heartbeat busy_since={busy_since}")
            return None
        payload = AgentHeartbeatCreate(busy_since=busy_since)
        try:
            return self._call(lambda: self._client().agent_heartbeat(payload))
        except MAPError as exc:
            raise WorkerError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise WorkerError(str(exc)) from exc
