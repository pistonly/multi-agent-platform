"""Remote FS P1: project content_root, source meta, inventory, delta CAS, auto-sync."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from map_fs import scan_plane, write_round_comment, write_topic_index
from map_types.schemas.fs import fs_projection_content_hash
from sqlalchemy import select

from server.domain.models import AuditLog, Project


def _create_project(client, admin_headers: dict, workspace: Path, key: str, **extra) -> dict:
    payload = {
        "project_key": key,
        "name": key,
        "workspace_path": str(workspace),
        **extra,
    }
    response = client.post("/api/v1/projects", headers=admin_headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _detail_read(topic) -> dict:
    from cli.commands.fs import fs_topic_to_detail_read

    return fs_topic_to_detail_read(topic).model_dump(mode="json")


def _push_plane(client, headers: dict, pid: str, workspace: Path, *, base_revision: int | None = None, content_root: str | None = "map") -> dict:
    plane = scan_plane(workspace, content_root or "map")
    topics = [_detail_read(t) for t in plane.topics]
    body = {
        "client_workspace": str(workspace),
        "base_revision": base_revision,
        "content_root": content_root,
        "topics": topics,
        "experiments": [],
    }
    response = client.put(f"/api/v1/projects/{pid}/fs/projection", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _seed_topic(workspace: Path, slug: str = "remote-demo", *, content_root: str = "map") -> None:
    write_topic_index(
        workspace,
        slug,
        title="Remote Demo",
        creator="host",
        participants=["participant"],
        content_root=content_root,
    )
    write_round_comment(
        workspace, slug, round_number=1, persona="host", body="# r1 host", content_root=content_root
    )


def test_project_content_root_isolated_scan(client, admin_headers, tmp_path) -> None:
    notes = tmp_path / "notes-ws"
    notes.mkdir()
    (notes / "notes").mkdir()
    write_topic_index(notes, "notes-only", title="Notes", creator="host", content_root="notes")
    other = tmp_path / "map-ws"
    other.mkdir()
    (other / "map").mkdir()
    write_topic_index(other, "map-only", title="Map", creator="host")

    notes_proj = _create_project(
        client, admin_headers, notes, f"fs-notes-{uuid.uuid4().hex[:6]}", content_root="notes"
    )
    map_proj = _create_project(
        client, admin_headers, other, f"fs-map-{uuid.uuid4().hex[:6]}"
    )
    assert notes_proj["content_root"] == "notes"
    assert map_proj["content_root"] == "map"

    notes_status = client.get(
        f"/api/v1/projects/{notes_proj['id']}/fs/status", headers=admin_headers
    ).json()
    map_status = client.get(
        f"/api/v1/projects/{map_proj['id']}/fs/status", headers=admin_headers
    ).json()
    assert notes_status["mode"] == "local-fs"
    assert notes_status["content_root"] == "notes"
    assert map_status["content_root"] == "map"

    notes_topics = client.get(
        f"/api/v1/projects/{notes_proj['id']}/fs/topics", headers=admin_headers
    ).json()
    map_topics = client.get(
        f"/api/v1/projects/{map_proj['id']}/fs/topics", headers=admin_headers
    ).json()
    assert {t["slug"] for t in notes_topics} == {"notes-only"}
    assert {t["slug"] for t in map_topics} == {"map-only"}


def test_push_content_root_mismatch_409(client, admin_headers, tmp_path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "map").mkdir()
    project = _create_project(client, admin_headers, tmp_path / "gone", f"fs-root-{uuid.uuid4().hex[:6]}")
    resp = client.put(
        f"/api/v1/projects/{project['id']}/fs/projection",
        headers=admin_headers,
        json={
            "client_workspace": str(ws),
            "content_root": "notes",
            "topics": [],
            "experiments": [],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "content_root_mismatch"


def test_source_meta_and_stale_sla(client, admin_headers, db_session, tmp_path) -> None:
    ws = tmp_path / "client-ws"
    ws.mkdir()
    _seed_topic(ws)
    project = _create_project(
        client, admin_headers, tmp_path / "gone", f"fs-stale-{uuid.uuid4().hex[:6]}",
        fs_freshness_sla_seconds=60,
    )
    _push_plane(client, admin_headers, project["id"], ws)
    row = db_session.get(Project, uuid.UUID(project["id"]))
    assert row is not None
    row.fs_freshness_sla_seconds = 1
    from server.domain.models import FsProjection

    proj = db_session.scalar(select(FsProjection).where(FsProjection.project_id == row.id))
    assert proj is not None
    proj.pushed_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    db_session.commit()

    status = client.get(f"/api/v1/projects/{project['id']}/fs/status", headers=admin_headers).json()
    assert status["source"]["content_source"] == "fs-projection"
    assert status["source"]["stale"] is True
    assert status["source"]["stale_reason"] == "freshness_sla_exceeded"
    assert status["source"]["source_revision"] == str(status["projection_revision"])

    topics = client.get(f"/api/v1/projects/{project['id']}/topics", headers=admin_headers).json()
    fs_topics = [t for t in topics if t.get("slug") == "remote-demo"]
    assert fs_topics
    assert fs_topics[0]["source"]["source_revision"] == status["source"]["source_revision"]

    me = client.get("/api/v1/agents/me", headers=admin_headers).json()
    # admin may have no project_id; use host-like work via project agent if needed
    work = client.get("/api/v1/agents/me/work", headers=admin_headers).json()
    if work.get("source"):
        assert work["source"]["source_revision"] == status["source"]["source_revision"]
    _ = me


def test_inventory_and_delta_cas(client, admin_headers, db_session, tmp_path) -> None:
    ws = tmp_path / "delta-ws"
    ws.mkdir()
    _seed_topic(ws)
    project = _create_project(client, admin_headers, tmp_path / "gone", f"fs-delta-{uuid.uuid4().hex[:6]}")
    first = _push_plane(client, admin_headers, project["id"], ws)
    inventory = client.get(
        f"/api/v1/projects/{project['id']}/fs/projection/inventory", headers=admin_headers
    ).json()
    assert inventory["projection_revision"] == 1
    assert len(inventory["objects"]) == 1
    assert inventory["objects"][0]["kind"] == "topic"

    write_round_comment(ws, "remote-demo", round_number=1, persona="participant", body="# p")
    topic = scan_plane(ws).topic_by_slug("remote-demo")
    assert topic is not None
    from cli.commands.fs import fs_topic_to_detail_read

    detail = fs_topic_to_detail_read(topic)
    result_topics = [detail]
    result_hash = fs_projection_content_hash(result_topics, [])
    delta = client.post(
        f"/api/v1/projects/{project['id']}/fs/projection/delta",
        headers=admin_headers,
        json={
            "base_revision": first["projection_revision"],
            "client_workspace": str(ws),
            "content_root": "map",
            "result_content_hash": result_hash,
            "changes": [
                {
                    "kind": "topic_upsert",
                    "slug": "remote-demo",
                    "value": detail.model_dump(mode="json"),
                }
            ],
        },
    )
    assert delta.status_code == 200, delta.text
    body = delta.json()
    assert body["projection_revision"] == 2
    assert body["noop"] is False

    write_round_comment(ws, "remote-demo", round_number=2, persona="host", body="# stale clone")
    stale_topic = scan_plane(ws).topic_by_slug("remote-demo")
    assert stale_topic is not None
    stale_detail = fs_topic_to_detail_read(stale_topic)
    stale_hash = fs_projection_content_hash([stale_detail], [])
    stale = client.post(
        f"/api/v1/projects/{project['id']}/fs/projection/delta",
        headers=admin_headers,
        json={
            "base_revision": 1,
            "client_workspace": str(ws),
            "content_root": "map",
            "result_content_hash": stale_hash,
            "changes": [
                {
                    "kind": "topic_upsert",
                    "slug": "remote-demo",
                    "value": stale_detail.model_dump(mode="json"),
                }
            ],
        },
    )
    assert stale.status_code == 409

    empty = client.post(
        f"/api/v1/projects/{project['id']}/fs/projection/delta",
        headers=admin_headers,
        json={
            "base_revision": 2,
            "client_workspace": str(ws),
            "content_root": "map",
            "result_content_hash": result_hash,
            "changes": [],
        },
    )
    assert empty.status_code == 200
    assert empty.json()["noop"] is True
    assert empty.json()["projection_revision"] == 2

    logs = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "fs.projection_delta",
                AuditLog.project_id == uuid.UUID(project["id"]),
            )
        )
    )
    assert logs
    assert logs[-1].payload_json["tombstones"] == 0


def test_delta_tombstone_required_for_delete(client, admin_headers, tmp_path) -> None:
    ws = tmp_path / "del-ws"
    ws.mkdir()
    _seed_topic(ws, "keep-me")
    _seed_topic(ws, "drop-me")
    project = _create_project(client, admin_headers, tmp_path / "gone", f"fs-del-{uuid.uuid4().hex[:6]}")
    first = _push_plane(client, admin_headers, project["id"], ws)
    inventory = client.get(
        f"/api/v1/projects/{project['id']}/fs/projection/inventory", headers=admin_headers
    ).json()
    drop_hash = next(o["content_hash"] for o in inventory["objects"] if o["slug"] == "drop-me")
    keep = scan_plane(ws).topic_by_slug("keep-me")
    assert keep is not None
    from cli.commands.fs import fs_topic_to_detail_read

    keep_detail = fs_topic_to_detail_read(keep)
    result_hash = fs_projection_content_hash([keep_detail], [])
    # Full PUT without the deleted topic still replaces in P0 repair API;
    # delta without tombstone must not drop drop-me if we only upsert keep-me.
    delta = client.post(
        f"/api/v1/projects/{project['id']}/fs/projection/delta",
        headers=admin_headers,
        json={
            "base_revision": first["projection_revision"],
            "client_workspace": str(ws),
            "content_root": "map",
            "result_content_hash": fs_projection_content_hash(
                [keep_detail, fs_topic_to_detail_read(scan_plane(ws).topic_by_slug("drop-me"))],
                [],
            ),
            "changes": [
                {"kind": "topic_upsert", "slug": "keep-me", "value": keep_detail.model_dump(mode="json")}
            ],
        },
    )
    assert delta.status_code == 200
    inventory_after = client.get(
        f"/api/v1/projects/{project['id']}/fs/projection/inventory", headers=admin_headers
    ).json()
    slugs = {o["slug"] for o in inventory_after["objects"]}
    assert slugs == {"keep-me", "drop-me"}

    tombstone = client.post(
        f"/api/v1/projects/{project['id']}/fs/projection/delta",
        headers=admin_headers,
        json={
            "base_revision": inventory_after["projection_revision"],
            "client_workspace": str(ws),
            "content_root": "map",
            "result_content_hash": result_hash,
            "changes": [
                {"kind": "topic_delete", "slug": "drop-me", "expected_hash": drop_hash}
            ],
        },
    )
    assert tombstone.status_code == 200, tombstone.text
    final = client.get(
        f"/api/v1/projects/{project['id']}/fs/projection/inventory", headers=admin_headers
    ).json()
    assert {o["slug"] for o in final["objects"]} == {"keep-me"}


def test_participant_delta_forbidden(client, admin_headers, tmp_path) -> None:
    ws = tmp_path / "acl-ws"
    ws.mkdir()
    _seed_topic(ws)
    project = _create_project(client, admin_headers, tmp_path / "gone", f"fs-acl-{uuid.uuid4().hex[:6]}")
    host_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": f"{project['project_key']}-host",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    host_headers = {"Authorization": f"Bearer {host_resp.json()['api_token']}"}
    first = _push_plane(client, host_headers, project["id"], ws)
    part = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": f"{project['project_key']}-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    part_headers = {"Authorization": f"Bearer {part.json()['api_token']}"}
    topic = scan_plane(ws).topics[0]
    from cli.commands.fs import fs_topic_to_detail_read

    detail = fs_topic_to_detail_read(topic)
    denied = client.post(
        f"/api/v1/projects/{project['id']}/fs/projection/delta",
        headers=part_headers,
        json={
            "base_revision": first["projection_revision"],
            "client_workspace": str(ws),
            "content_root": "map",
            "result_content_hash": fs_projection_content_hash([detail], []),
            "changes": [
                {"kind": "topic_upsert", "slug": topic.slug, "value": detail.model_dump(mode="json")}
            ],
        },
    )
    assert denied.status_code == 403


def test_tombstone_requires_expected_hash(client, admin_headers, tmp_path) -> None:
    ws = tmp_path / "hash-ws"
    ws.mkdir()
    _seed_topic(ws, "keep-me")
    _seed_topic(ws, "drop-me")
    project = _create_project(client, admin_headers, tmp_path / "gone", f"fs-hash-{uuid.uuid4().hex[:6]}")
    first = _push_plane(client, admin_headers, project["id"], ws)
    keep = scan_plane(ws).topic_by_slug("keep-me")
    assert keep is not None
    from cli.commands.fs import fs_topic_to_detail_read

    keep_detail = fs_topic_to_detail_read(keep)
    missing_hash = client.post(
        f"/api/v1/projects/{project['id']}/fs/projection/delta",
        headers=admin_headers,
        json={
            "base_revision": first["projection_revision"],
            "client_workspace": str(ws),
            "content_root": "map",
            "result_content_hash": fs_projection_content_hash([keep_detail], []),
            "changes": [{"kind": "topic_delete", "slug": "drop-me"}],
        },
    )
    assert missing_hash.status_code in {409, 422}


def test_experiment_and_work_share_source_revision(client, admin_headers, tmp_path) -> None:
    from tests._frontmatter import make_valid_plan

    ws = tmp_path / "src-ws"
    ws.mkdir()
    _seed_topic(ws)
    project = _create_project(client, admin_headers, tmp_path / "gone", f"fs-src-{uuid.uuid4().hex[:6]}")
    _push_plane(client, admin_headers, project["id"], ws)
    status = client.get(f"/api/v1/projects/{project['id']}/fs/status", headers=admin_headers).json()
    revision = status["source"]["source_revision"]

    host_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": f"{project['project_key']}-host",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert host_resp.status_code == 201, host_resp.text
    host_headers = {"Authorization": f"Bearer {host_resp.json()['api_token']}"}
    work = client.get("/api/v1/agents/me/work", headers=host_headers).json()
    assert work["source"]["source_revision"] == revision

    created = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=host_headers,
        json={"title": "FS source experiment", "plan": {"content_md": make_valid_plan()}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"]["source_revision"] == revision
    listed = client.get(
        f"/api/v1/projects/{project['id']}/experiments", headers=host_headers
    ).json()
    assert listed[0]["source"]["source_revision"] == revision

