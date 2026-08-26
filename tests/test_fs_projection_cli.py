"""CLI helpers for remote FS P1: sync states, tombstone gating, auto-sync."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer
from map_client.exceptions import MAPHTTPError
from map_types.schemas.fs import FsTopicDetailRead

from cli.fs_projection import (
    compute_changes,
    describe_sync_state,
    maybe_auto_sync,
    sync_projection,
)
from server.services.fs_source_service import FsProjectionTooLargeError, _payload_size_ok


def _inventory(**overrides):
    data = {
        "content_root": "map",
        "content_hash": "abc",
        "publisher_agent_id": None,
        "projection_revision": 1,
        "objects": [],
        "source": SimpleNamespace(stale=False, stale_reason=None),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_describe_sync_state_matrix() -> None:
    assert (
        describe_sync_state(
            inventory=None,
            local_hash="a",
            local_content_root="map",
            publisher_ok=True,
            server_mode="local-fs",
        )
        == "in-sync"
    )
    assert (
        describe_sync_state(
            inventory=None,
            local_hash="a",
            local_content_root="map",
            publisher_ok=True,
            server_mode="detached",
        )
        == "detached"
    )
    assert (
        describe_sync_state(
            inventory=_inventory(content_root="notes"),
            local_hash="a",
            local_content_root="map",
            publisher_ok=True,
            server_mode="projection-cache",
        )
        == "divergent"
    )
    assert (
        describe_sync_state(
            inventory=_inventory(publisher_agent_id="other"),
            local_hash="a",
            local_content_root="map",
            publisher_ok=False,
            server_mode="projection-cache",
        )
        == "divergent"
    )
    assert (
        describe_sync_state(
            inventory=_inventory(source=SimpleNamespace(stale=True)),
            local_hash="abc",
            local_content_root="map",
            publisher_ok=True,
            server_mode="projection-cache",
        )
        == "stale"
    )
    assert (
        describe_sync_state(
            inventory=_inventory(content_hash="abc"),
            local_hash="abc",
            local_content_root="map",
            publisher_ok=True,
            server_mode="projection-cache",
        )
        == "in-sync"
    )
    assert (
        describe_sync_state(
            inventory=_inventory(content_hash="old"),
            local_hash="new",
            local_content_root="map",
            publisher_ok=True,
            server_mode="projection-cache",
        )
        == "local-ahead"
    )


def test_compute_changes_requires_explicit_delete() -> None:
    remote = _inventory(
        objects=[
            SimpleNamespace(kind="topic", slug="gone", content_hash="deadbeef"),
            SimpleNamespace(kind="topic", slug="keep", content_hash="cafe"),
        ]
    )
    keep = FsTopicDetailRead(
        id="00000000-0000-0000-0000-000000000001",
        slug="keep",
        title="Keep",
        creator="host",
        dir_path="map/topics/keep",
    )
    changes, summary = compute_changes(
        local_topics=[keep],
        local_experiments=[],
        inventory=remote,
        full=False,
    )
    kinds = {change.kind for change in changes}
    assert "topic_delete" in kinds
    assert any(row["action"] == "delete" and row["slug"] == "gone" for row in summary)
    upsert_only = [change for change in changes if change.kind != "topic_delete"]
    assert all(change.kind.endswith("_upsert") or change.kind == "topic_upsert" for change in upsert_only)


def test_payload_rejects_oversized_object() -> None:
    huge = FsTopicDetailRead(
        id="00000000-0000-0000-0000-000000000002",
        slug="huge",
        title="Huge",
        creator="host",
        dir_path="map/topics/huge",
        description="x" * (1024 * 1024 + 64),
    )
    with pytest.raises(FsProjectionTooLargeError, match="huge"):
        _payload_size_ok([huge], [])


def test_sync_skips_deletes_when_not_allowed(monkeypatch) -> None:
    remote = _inventory(
        objects=[SimpleNamespace(kind="topic", slug="gone", content_hash="deadbeef")],
        projection_revision=4,
    )

    class Client:
        def fs_projection_inventory(self, pid):
            return remote

        def fs_push_projection(self, *args, **kwargs):
            raise AssertionError("must not PUT when deletes are skipped")

        def fs_apply_projection_delta(self, *args, **kwargs):
            raise AssertionError("must not delta when deletes are skipped")

    monkeypatch.setattr(
        "cli.fs_projection.local_projection_objects",
        lambda workspace: ([], []),
    )
    monkeypatch.setattr(
        "cli.fs_projection.fs_projection_content_hash",
        lambda topics, experiments: "local-hash",
    )
    monkeypatch.setattr("cli.fs_projection._workspace_root", lambda workspace: "map")
    result = sync_projection(
        Client(),
        pid="00000000-0000-0000-0000-000000000003",
        workspace=__import__("pathlib").Path("."),
        allow_deletes=False,
    )
    assert result["sync_state"] == "skipped-deletes"
    assert result["skipped_deletes"][0]["slug"] == "gone"


def test_maybe_auto_sync_http_error_exits(monkeypatch) -> None:
    class Status:
        mode = "projection-cache"

    class Client:
        def fs_plane_status(self, pid):
            return Status()

    monkeypatch.setattr("cli.main._cli_options", {"persona": "host", "dry_run": False})
    monkeypatch.setattr("cli.main.resolve_client", lambda **kwargs: Client())
    # T23: fs_projection resolves _resolve_project via the runner module ref.
    monkeypatch.setattr(
        "cli.runner._resolve_project",
        lambda *args, **kwargs: "00000000-0000-0000-0000-000000000004",
    )
    monkeypatch.setattr(
        "cli.fs_projection.sync_projection",
        lambda *args, **kwargs: (_ for _ in ()).throw(MAPHTTPError(403, "forbidden")),
    )
    with pytest.raises(typer.Exit) as exc:
        maybe_auto_sync(no_sync=False, workspace=__import__("pathlib").Path("."))
    assert exc.value.exit_code == 1
