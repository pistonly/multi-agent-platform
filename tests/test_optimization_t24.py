"""T24：simple-waker 热路径走 in-process MAPClient，不再起 map 子进程。"""

from __future__ import annotations

import uuid
from pathlib import Path
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


# ---------------------------------------------------------------------------
# T24 2/2：e2e driver 读查询迁 in-process SDK client
# ---------------------------------------------------------------------------


class _StubTopicRead:
    def model_dump(self, mode: str = "json") -> dict:
        return {"id": "11111111-1111-1111-1111-111111111111", "status": "open", "title": "demo"}


class _StubExperimentSummary:
    def model_dump(self, mode: str = "json") -> dict:
        return {
            "id": "22222222-2222-2222-2222-222222222222",
            "topic_id": "11111111-1111-1111-1111-111111111111",
            "phase": "review",
        }


class _StubExperimentDetail:
    def model_dump(self, mode: str = "json") -> dict:
        return {"id": "22222222-2222-2222-2222-222222222222", "phase": "running"}


class _E2EStubSdk:
    def __init__(self) -> None:
        self.topic_calls: list[uuid.UUID] = []
        self.list_experiments_calls: list[uuid.UUID] = []

    def get_topic(self, topic_id: uuid.UUID):
        self.topic_calls.append(topic_id)
        return _StubTopicRead()

    def list_experiments(self, project_id: uuid.UUID, *, phase=None, **kwargs):
        self.list_experiments_calls.append(project_id)
        return [_StubExperimentSummary()]


def _make_e2e_client() -> tuple[MapSdkClient, _E2EStubSdk]:
    from map_client.project_config import ProjectMapConfig

    stub = _E2EStubSdk()
    client = MapSdkClient(persona="host", sdk=stub)
    client._cfg = ProjectMapConfig(
        map_dir=Path("/tmp/.map"),
        api_url="http://localhost:18400",
        project_key="demo",
        project_id="99999999-9999-9999-9999-999999999999",
        default_persona="host",
        personas={},
        tokens={},
    )
    return client, stub


def test_topic_show_dumps_get_topic() -> None:
    client, stub = _make_e2e_client()
    data = client.topic_show("11111111-1111-1111-1111-111111111111")
    assert stub.topic_calls == [uuid.UUID("11111111-1111-1111-1111-111111111111")]
    assert data["status"] == "open"


def test_experiment_list_falls_back_to_sdk_without_workspace(monkeypatch) -> None:
    import cli.experiment_fs as experiment_fs

    monkeypatch.setattr(experiment_fs, "should_scan_local_experiments", lambda *a, **k: False)
    client, stub = _make_e2e_client()
    rows = client.experiment_list()
    assert stub.list_experiments_calls == [uuid.UUID("99999999-9999-9999-9999-999999999999")]
    assert rows[0]["phase"] == "review"


def test_experiment_list_merges_local_workspace(monkeypatch) -> None:
    import cli.commands.experiment as commands_experiment
    import cli.experiment_fs as experiment_fs

    monkeypatch.setattr(experiment_fs, "should_scan_local_experiments", lambda *a, **k: True)
    monkeypatch.setattr(experiment_fs, "workspace_root", lambda: Path("/tmp/ws"))
    monkeypatch.setattr(experiment_fs, "iter_indexed_experiments", lambda workspace: [])
    monkeypatch.setattr(
        experiment_fs,
        "merge_experiment_summaries",
        lambda fs_items, api_items, pid, workspace: api_items,
    )
    monkeypatch.setattr(
        experiment_fs, "filter_experiment_summaries", lambda items, **kw: list(items)
    )
    monkeypatch.setattr(
        commands_experiment,
        "_list_api_experiments_all",
        lambda client, pid, **kw: [_StubExperimentSummary()],
    )

    client, stub = _make_e2e_client()
    rows = client.experiment_list(phase="review")
    # workspace 合并路径下不再走裸 SDK list_experiments
    assert stub.list_experiments_calls == []
    assert rows[0]["topic_id"] == "11111111-1111-1111-1111-111111111111"


def test_experiment_status_reuses_load_experiment(monkeypatch) -> None:
    import cli.commands.experiment as commands_experiment

    seen: dict = {}

    def fake_load(client, raw):
        seen["client"] = client
        seen["raw"] = raw
        return _StubExperimentDetail()

    monkeypatch.setattr(commands_experiment, "_load_experiment", fake_load)
    client, stub = _make_e2e_client()
    data = client.experiment_status("22222222-2222-2222-2222-222222222222")
    assert seen["client"] is stub
    assert seen["raw"] == "22222222-2222-2222-2222-222222222222"
    assert data["phase"] == "running"


def test_e2e_driver_uses_sdk_client(tmp_path: Path) -> None:
    from cli.e2e_collab import E2EDriver, Scenario

    scenario = Scenario(
        subject="s",
        topic_title="t",
        project_root=tmp_path,
        run_dir=tmp_path / "run",
        plan_file=tmp_path / "plan.md",
        log_file=tmp_path / "log.md",
    )
    driver = E2EDriver(scenario=scenario, clients={})
    assert isinstance(driver.map_host, MapSdkClient)
    driver.map_host.close()
