import json

import pytest

from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.host_worker_types import WorkerError


def test_load_bridge_state_returns_default_collections_for_missing_file(tmp_path):
    state = load_bridge_state(
        tmp_path / "missing.json",
        bridge_name="host",
        default_collections=("topics", "experiments"),
    )

    assert state == {"schema_version": 1, "topics": {}, "experiments": {}}


def test_load_bridge_state_rejects_invalid_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bad", encoding="utf-8")

    with pytest.raises(WorkerError, match="Invalid host bridge state file"):
        load_bridge_state(path, bridge_name="host", default_collections=("topics",))


def test_load_bridge_state_validates_schema_by_default(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")

    with pytest.raises(WorkerError, match="Unsupported host bridge state schema"):
        load_bridge_state(path, bridge_name="host", default_collections=("topics",))


def test_load_bridge_state_can_skip_schema_validation(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")

    state = load_bridge_state(
        path,
        bridge_name="reviewer",
        default_collections=("experiments", "resolved_items"),
        validate_schema=False,
    )

    assert state == {"schema_version": 2, "experiments": {}, "resolved_items": {}}


def test_save_bridge_state_writes_atomically_readable_json(tmp_path):
    path = tmp_path / "state.json"

    save_bridge_state(path, {"schema_version": 1, "topics": {"t1": {"done": True}}})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "topics": {"t1": {"done": True}},
    }
    assert not (tmp_path / "state.json.tmp").exists()
