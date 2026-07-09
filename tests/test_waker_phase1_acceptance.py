"""Phase 1 acceptance tests for the runtime-waker inbound_event gate.

Covers A1–A5 from plan v2:
- A1: replay rejection — 100x POST /me/inbound-events with same fingerprint,
  1 success + 99 conflicts, exactly 1 inbound_event row.
- A2: concurrent dedup — N threads racing with same fingerprint, exactly 1
  wins (file-backed sqlite so each thread owns its session).
- A3: three-way audit join — notification.id == inbound_event.event_id ==
  sessions jsonl.event_id, no gaps, no drift.
- A4: P95 latency baseline by wake kind. Recorded only, no threshold
  assertion — baseline is for Phase 2 SSE comparison.
- A5: Phase 1 source invariant — every inbound_event row + every sessions
  jsonl row has source/event_source == "polling".

A1/A3/A5 reuse the shared `client` fixture from conftest.py so the FK
relationships + auth pipeline run exactly like prod. A2 needs independent
sessions per thread, so it builds its own file-backed sqlite (StaticPool
would serialize threads through one connection and mask the race).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

import json
import threading
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from map_types.enums import InboundEventSource
from map_types.schemas import InboundEventCreate
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from server.db.base import Base
from server.db.session import get_db
from server.main import create_app

# --- A2 helper: per-thread DB -------------------------------------------------


def _seed_threaded_app(tmp_path: Path) -> tuple[TestClient, Any]:
    """Build a TestClient against a file-backed sqlite (multi-conn safe).

    StaticPool pins every session to one connection, which masks the race A2
    wants to surface. NullPool gives each session its own connection while
    still pointing at the same file (and the same UNIQUE(fingerprint)
    constraint on disk).
    """
    db_path = tmp_path / "phase1_a2.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    app = create_app(init_db_on_startup=False)

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), SessionLocal


def _make_admin(client: TestClient) -> tuple[str, str]:
    res = client.post("/api/v1/agents", json={"name": "admin-a2", "role": "admin"})
    assert res.status_code == 201, res.text
    data = res.json()
    return data["id"], data["api_token"]


def _make_project(client: TestClient, headers: dict[str, str], key: str) -> dict:
    res = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "project_key": key,
            "name": key,
            "workspace_path": f"/tmp/{key}",
            "description": "",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _make_agent(
    client: TestClient,
    headers: dict[str, str],
    project: dict,
    name: str,
) -> tuple[str, str]:
    res = client.post(
        "/api/v1/agents",
        headers=headers,
        json={"name": name, "role": "agent", "project_key": project["project_key"]},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["id"], data["api_token"]


def _post_inbound(
    client: TestClient,
    headers: dict[str, str],
    *,
    event_id: uuid.UUID,
    fingerprint: str,
    event_type: str = "pending_topic_reply",
) -> int:
    payload = InboundEventCreate(
        event_id=event_id,
        event_type=event_type,
        source=InboundEventSource.polling,
        fingerprint=fingerprint,
    ).model_dump(mode="json")
    res = client.post("/api/v1/agents/me/inbound-events", headers=headers, json=payload)
    return res.status_code


# --- A1 ---------------------------------------------------------------------


def test_a1_replay_rejection_returns_409_after_first_success(
    client, admin_headers, project, agent_token, db_session
) -> None:
    """A1: 100 sequential POSTs of the same fingerprint → 1×201, 99×409, 1 row.

    Exercises the live server gate, not a mock — the UNIQUE(fingerprint)
    constraint must reject replays at the DB level, surfacing as 409.
    """
    from server.domain.models import InboundEvent

    _, token = agent_token
    headers = {"Authorization": f"Bearer {token}"}

    fingerprint = "host:pending_topic_reply:topic-A:comment-A"
    event_id = uuid.uuid4()

    statuses = [
        _post_inbound(client, headers, event_id=event_id, fingerprint=fingerprint)
        for _ in range(100)
    ]
    successes = sum(1 for s in statuses if s == 201)
    conflicts = sum(1 for s in statuses if s == 409)
    other = [s for s in statuses if s not in (201, 409)]

    assert other == [], f"unexpected non-201/409 responses: {other}"
    assert successes == 1, f"expected exactly 1 success, got {successes}"
    assert conflicts == 99, f"expected 99 conflicts, got {conflicts}"

    # DB invariant: exactly 1 row for the fingerprint.
    db_session.expire_all()
    rows = db_session.query(InboundEvent).filter(InboundEvent.fingerprint == fingerprint).all()
    assert len(rows) == 1
    assert str(rows[0].event_id) == str(event_id)
    assert rows[0].source == InboundEventSource.polling


# --- A2 ---------------------------------------------------------------------


def test_a2_concurrent_dedup_single_writer_wins(tmp_path) -> None:
    """A2: N threads racing with same fingerprint → exactly 1×201, (N-1)×409.

    Each thread owns its DB session via the file-backed sqlite override, so
    the UNIQUE(fingerprint) constraint is the actual gate (not an in-process
    lock). Threading is required by plan A2 to expose the real race.
    """
    client, SessionLocal = _seed_threaded_app(tmp_path)
    _, admin_token = _make_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    project = _make_project(client, admin_headers, "a2-project")
    _, agent_token = _make_agent(client, admin_headers, project, "agent-a2")
    agent_headers = {"Authorization": f"Bearer {agent_token}"}

    fingerprint = "host:pending_topic_reply:topic-A2:comment-A2"
    event_id = uuid.uuid4()
    n_threads = 10
    barrier = threading.Barrier(n_threads)
    results: list[int] = [0] * n_threads

    def worker(idx: int) -> None:
        barrier.wait()
        results[idx] = _post_inbound(
            client, agent_headers, event_id=event_id, fingerprint=fingerprint
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = sum(1 for r in results if r == 201)
    conflicts = sum(1 for r in results if r == 409)
    assert successes == 1, f"expected 1 success across {n_threads} threads, got {successes}"
    assert conflicts == n_threads - 1

    from server.domain.models import InboundEvent

    with SessionLocal() as session:
        rows = session.query(InboundEvent).filter(InboundEvent.fingerprint == fingerprint).all()
        assert len(rows) == 1


def test_a2_sequential_duplicate_claim_returns_409(client, admin_headers, project, agent_token, db_session) -> None:
    """A2 (waker side): a second sequential claim of the same fingerprint is 409.

    With a single in-process session, sequential claims still hit the UNIQUE
    constraint on commit — this is the path a slow-but-correct waker takes
    after a crash and replay.
    """
    from server.domain.models import InboundEvent

    _, token = agent_token
    headers = {"Authorization": f"Bearer {token}"}
    fingerprint = "host:pending_topic_reply:topic-A2S:comment-A2S"
    event_id = uuid.uuid4()

    first = _post_inbound(client, headers, event_id=event_id, fingerprint=fingerprint)
    second = _post_inbound(client, headers, event_id=event_id, fingerprint=fingerprint)
    third = _post_inbound(client, headers, event_id=event_id, fingerprint=fingerprint)

    assert first == 201
    assert second == 409
    assert third == 409

    db_session.expire_all()
    rows = (
        db_session.query(InboundEvent)
        .filter(InboundEvent.fingerprint == fingerprint)
        .all()
    )
    assert len(rows) == 1


# --- A3 ---------------------------------------------------------------------


def test_a3_three_way_audit_join(client, admin_headers, project, agent_token, db_session) -> None:
    """A3: notification.id == inbound_event.event_id == sessions jsonl.event_id.

    End-to-end path: a second agent creates a topic (notification fires for
    the waker agent via fan-out, actor excluded) → waker records
    inbound_event against that notification.id → sessions jsonl entry
    written by append_session_wake_log. All three layers share the same
    event_id UUID.
    """
    from cli.session_wake_log import append_session_wake_log, resolve_session_log_path
    from server.domain.models import InboundEvent, Notification

    _, token = agent_token
    waker_headers = {"Authorization": f"Bearer {token}"}

    # Add a second agent (the "creator") so the waker receives the fan-out
    # notification. enqueue_from_event excludes the actor.
    _, creator_token = _make_agent(client, admin_headers, project, "agent-a3-creator")
    creator_headers = {"Authorization": f"Bearer {creator_token}"}

    topic_res = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=creator_headers,
        json={"title": "A3 join topic", "description": "round 1"},
    )
    assert topic_res.status_code == 201, topic_res.text
    topic = topic_res.json()

    notif_res = client.get("/api/v1/agents/me/notifications", headers=waker_headers)
    assert notif_res.status_code == 200
    items = notif_res.json()["items"]
    target = next(
        (
            n
            for n in items
            if n.get("target_type") == "topic" and n.get("target_id") == topic["id"]
        ),
        None,
    )
    assert target is not None, f"no notification for new topic; got {items}"
    notification_id = target["id"]

    fingerprint = f"host:pending_topic_replies:{notification_id}"
    status = _post_inbound(
        client,
        waker_headers,
        event_id=uuid.UUID(notification_id),
        fingerprint=fingerprint,
        event_type="pending_topic_replies",
    )
    assert status == 201

    log_dir = Path("/tmp/map-a3-sessions")  # noqa: S108
    log_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"sess-a3-{uuid.uuid4().hex[:8]}"
    append_session_wake_log(
        log_dir=log_dir,
        session_id=session_id,
        persona="host",
        integration="waker",
        prompt="wake for A3",
        response_text="handled",
        status="ok",
        event_id=notification_id,
        event_source="polling",
        fingerprint=fingerprint,
    )

    db_session.expire_all()
    notif = (
        db_session.query(Notification)
        .filter(Notification.id == uuid.UUID(notification_id))
        .one()
    )
    ib = (
        db_session.query(InboundEvent)
        .filter(InboundEvent.fingerprint == fingerprint)
        .one()
    )
    assert str(notif.id) == str(ib.event_id), (
        f"notification↔inbound_event drift: notif.id={notif.id} vs ib.event_id={ib.event_id}"
    )

    log_path = resolve_session_log_path(log_dir, session_id, "host")
    assert log_path.is_file(), f"sessions jsonl not written at {log_path}"
    entry = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["event_id"] == notification_id, (
        f"sessions jsonl↔notification drift: entry.event_id={entry['event_id']} vs notif.id={notification_id}"
    )
    assert entry["fingerprint"] == fingerprint

    log_path.unlink()


# --- A4 ---------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def test_a4_p95_baseline_by_kind(tmp_path, capsys) -> None:
    """A4: P95 wake latency by kind. Records only — no threshold assertion.

    Baseline purpose: Phase 2 SSE overlay compares against these numbers to
    decide if SSE actually moves the needle. With no threshold the test is
    intentionally tolerant of CI noise. Per-kind samples use only the jsonl
    append (which is the part SSE wouldn't change) to keep the loop fast.
    """
    from cli.session_wake_log import append_session_wake_log

    kinds = ["mentions", "pending_reviews", "pending_topic_replies", "my_open_experiments"]
    samples: dict[str, list[float]] = defaultdict(list)
    iterations_per_kind = 25
    for kind in kinds:
        for i in range(iterations_per_kind):
            t0 = datetime.now(UTC).timestamp()
            log_dir = tmp_path / f"sessions-{kind}"
            append_session_wake_log(
                log_dir=log_dir,
                session_id=f"sess-{kind}-{i}",
                persona="host",
                integration="waker",
                prompt=f"MAP wake · {kind} · obj-{i}",
                response_text="ok",
                status="ok",
                event_id=str(uuid.uuid4()),
                event_source="polling",
                fingerprint=f"host:{kind}:obj-{i}:v1",
            )
            t1 = datetime.now(UTC).timestamp()
            samples[kind].append(t1 - t0)

    p95_by_kind: dict[str, float] = {}
    stats_by_kind: dict[str, dict[str, float]] = {}
    for kind, latencies in samples.items():
        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)
        p95_by_kind[kind] = p95
        stats_by_kind[kind] = {"p50": p50, "p95": p95, "p99": p99}
        print(f"[A4 baseline] kind={kind} n={len(latencies)} p50={p50:.6f}s p95={p95:.6f}s p99={p99:.6f}s")

    assert set(p95_by_kind) == set(kinds)
    for v in p95_by_kind.values():
        assert v >= 0.0
        assert v < 5.0, f"A4 baseline p95 suspiciously high ({v:.3f}s) — capture for triage"

    captured = capsys.readouterr()
    for kind in kinds:
        assert f"kind={kind}" in captured.out

    # Stable artifact under .map/perf-baselines/ so Phase 2 comparison has
    # a known path. tmp_path would be cleaned up after the test.
    repo_root = Path(__file__).resolve().parent.parent
    baseline_path = repo_root / ".map" / "perf-baselines" / "phase1-p95-baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(
            {
                "phase": "phase1-polling",
                "iterations_per_kind": iterations_per_kind,
                "p50_seconds_by_kind": {k: stats_by_kind[k]["p50"] for k in kinds},
                "p95_seconds_by_kind": p95_by_kind,
                "p99_seconds_by_kind": {k: stats_by_kind[k]["p99"] for k in kinds},
                "captured_at": datetime.now(UTC).isoformat(),
                "note": (
                    "jsonl-append-only baseline; A4 is informational, no threshold. "
                    "Phase 2 SSE overlay should re-run this test and diff."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert baseline_path.exists()


# --- A5 ---------------------------------------------------------------------


def test_a5_phase1_source_invariant(client, admin_headers, project, agent_token, db_session) -> None:
    """A5: every inbound_event row + sessions jsonl row is "polling" in Phase 1.

    This is the Phase 1 commitment that makes Phase 2 SSE an additive change:
    once we know all current rows are polling, adding a `sse` source can be
    diffed and reasoned about without auditing historical rows.
    """
    from cli.session_wake_log import append_session_wake_log
    from server.domain.models import InboundEvent

    _, token = agent_token
    agent_headers = {"Authorization": f"Bearer {token}"}

    kinds = ["mentions", "pending_topic_replies", "pending_advance_rounds", "my_open_experiments"]
    for i, kind in enumerate(kinds):
        fp = f"host:{kind}:a5-obj-{i}:v1"
        event_id = uuid.uuid4()
        status = _post_inbound(
            client, agent_headers, event_id=event_id, fingerprint=fp, event_type=kind
        )
        assert status == 201

    db_session.expire_all()
    rows = db_session.query(InboundEvent).all()
    assert len(rows) == len(kinds)
    bad = [r for r in rows if r.source != InboundEventSource.polling]
    assert not bad, f"A5 violation — non-polling inbound_event rows: {[r.source for r in bad]}"

    log_dir = Path("/tmp/map-a5-sessions")  # noqa: S108
    log_dir.mkdir(parents=True, exist_ok=True)
    for p in log_dir.glob("sess-a5-*.jsonl"):
        p.unlink()

    for i, kind in enumerate(kinds):
        append_session_wake_log(
            log_dir=log_dir,
            session_id=f"sess-a5-{i}",
            persona="host",
            integration="waker",
            prompt=f"MAP wake · {kind} · a5-obj-{i}",
            response_text="ok",
            status="ok",
            event_id=str(uuid.uuid4()),
            event_source="polling",
            fingerprint=f"host:{kind}:a5-obj-{i}:v1",
        )

    jsonl_files = list(log_dir.glob("*sess-a5-*.jsonl"))
    assert len(jsonl_files) == len(kinds)
    for path in jsonl_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            assert entry["event_source"] == "polling", (
                f"A5 violation in {path.name}: event_source={entry['event_source']}"
            )
    for p in jsonl_files:
        p.unlink()
