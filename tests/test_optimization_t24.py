"""T24：simple-waker 热路径走 in-process MAPClient，不再起 map 子进程。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from map_client.exceptions import MAPConflictError, MAPServerError
from map_types.schemas.inbound_event import InboundEventCreate

from cli.errors import WorkerError
from cli.map_command_client import MapCommandClient
from cli.map_sdk_client import MapSdkClient
from cli.simple_waker import build_waker_client


class _StubWork:
    def model_dump(self, mode: str = "json") -> dict:
        assert mode == "json"
        return {
            "agent": {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "host"},
            "topic_progress": {"items": [], "total": 0},
            "todos": {"action_items": []},
            "notifications": {"items": [], "total": 0, "unread_count": 0},
        }


class _StubMe:
    def model_dump(self, mode: str = "json") -> dict:
        return {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "host"}


class _StubSdk:
    def __init__(self) -> None:
        self.work_kwargs: dict | None = None
        self.closed = False
        self.inbound: InboundEventCreate | None = None
        self.wake_ids: list[uuid.UUID] = []
        self.conflict = False
        self.fail_work = False

    def get_agent_work(self, **kwargs):
        self.work_kwargs = kwargs
        if self.fail_work:
            raise MAPServerError(503, "unavailable")
        return _StubWork()

    def get_me(self):
        return _StubMe()

    def record_inbound_event(self, payload: InboundEventCreate):
        self.inbound = payload
        if self.conflict:
            raise MAPConflictError(409, "duplicate fingerprint")
        return SimpleNamespace(status="recorded")

    def mark_wake_sent(self, action_item_id: uuid.UUID):
        self.wake_ids.append(action_item_id)
        return SimpleNamespace(model_dump=lambda mode="json": {"id": str(action_item_id)})

    def scan_stalled_experiment_locks(self):
        return SimpleNamespace(model_dump=lambda mode="json": {"emitted_count": 2, "notification_ids": []})

    def close(self) -> None:
        self.closed = True


def test_work_requests_wakeable_waker_client() -> None:
    stub = _StubSdk()
    client = MapSdkClient(persona="host", sdk=stub)
    data = client.work()
    assert stub.work_kwargs == {"notification_category": "wakeable", "client": "waker"}
    assert data["agent"]["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert data["todos"]["action_items"] == []


def test_whoami_dumps_agent() -> None:
    client = MapSdkClient(persona="host", sdk=_StubSdk())
    assert client.whoami()["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_inbound_event_record_true_then_conflict_false() -> None:
    stub = _StubSdk()
    client = MapSdkClient(persona="host", sdk=stub)
    event_id = str(uuid.uuid4())
    assert client.inbound_event_record(
        event_id=event_id,
        fingerprint="fp-1",
        event_type="simple-waker.remind",
        source="polling",
    )
    assert stub.inbound is not None
    assert stub.inbound.fingerprint == "fp-1"
    stub.conflict = True
    assert (
        client.inbound_event_record(
            event_id=event_id,
            fingerprint="fp-1",
            event_type="simple-waker.remind",
        )
        is False
    )


def test_mark_wake_sent_and_scan_stalled() -> None:
    stub = _StubSdk()
    client = MapSdkClient(persona="host", sdk=stub)
    item_id = str(uuid.uuid4())
    client.action_mark_wake_sent(item_id)
    assert stub.wake_ids == [uuid.UUID(item_id)]
    scanned = client.experiment_scan_stalled_locks()
    assert scanned is not None
    assert scanned["emitted_count"] == 2


def test_dry_run_skips_writes() -> None:
    stub = _StubSdk()
    client = MapSdkClient(persona="host", sdk=stub, dry_run=True)
    assert client.inbound_event_record(
        event_id=str(uuid.uuid4()),
        fingerprint="fp",
        event_type="simple-waker.remind",
    )
    assert stub.inbound is None
    client.action_mark_wake_sent(str(uuid.uuid4()))
    assert stub.wake_ids == []
    assert client.experiment_scan_stalled_locks() is None


def test_http_error_becomes_worker_error() -> None:
    stub = _StubSdk()
    stub.fail_work = True
    client = MapSdkClient(persona="host", sdk=stub)
    with pytest.raises(WorkerError, match="unavailable"):
        client.work()


def test_request_error_becomes_worker_error() -> None:
    class Boom:
        def get_me(self):
            raise httpx.ConnectError(
                "refused",
                request=httpx.Request("GET", "http://127.0.0.1:9/agents/me"),
            )

    client = MapSdkClient(persona="host", sdk=Boom())
    with pytest.raises(WorkerError, match="refused"):
        client.whoami()


def test_close_only_owned_sdk() -> None:
    stub = _StubSdk()
    injected = MapSdkClient(persona="host", sdk=stub)
    injected.close()
    assert stub.closed is False
    owned = MapSdkClient(persona="host")
    owned._sdk = stub
    owned._owns_sdk = True
    owned.close()
    assert stub.closed is True


def test_build_waker_client_defaults_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAP_WAKER_SUBPROCESS", raising=False)
    client = build_waker_client(persona="host", project_root=None, map_cmd="map", dry_run=False)
    assert isinstance(client, MapSdkClient)
    assert not isinstance(client, MapCommandClient)


def test_build_waker_client_subprocess_flag() -> None:
    client = build_waker_client(
        persona="host",
        project_root=None,
        map_cmd="map",
        dry_run=False,
        subprocess_client=True,
    )
    assert isinstance(client, MapCommandClient)


def test_build_waker_client_subprocess_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAP_WAKER_SUBPROCESS", "1")
    client = build_waker_client(persona="host", project_root=None, map_cmd="map", dry_run=False)
    assert isinstance(client, MapCommandClient)
