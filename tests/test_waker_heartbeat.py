"""Waker-heartbeat visibility (experiment 1b605e0b, topic waker-heartbeat-visibility).

Locks the D1 semantics at the API + service layer:
- plain ``GET /agents/me/work`` refreshes only ``last_api_seen_at``;
- a waker-marked call (``?client=waker``) refreshes BOTH timestamps — so the
  degraded host-invoke scenario (manual ``map work`` while waker is down) never
  pollutes liveness, which is read only from ``last_waker_poll_at``;
- null ``last_waker_poll_at`` → ``never`` (NOT stale, no WARN);
- ``stale`` is True only when ever-heartbeated AND older than the threshold;
- ``GET /status`` ``waker_heartbeats[]`` shape (per-agent rows).
"""

from datetime import datetime, timedelta, timezone


def _agent_row(db, agent_id):
    import uuid

    from server.domain.models import Agent

    return db.get(Agent, uuid.UUID(agent_id))


def _call_work(client, headers, *, waker=False):
    url = "/api/v1/agents/me/work"
    if waker:
        url += "?client=waker"
    return client.get(url, headers=headers)


def test_plain_call_refreshes_only_api_seen(client, auth_headers, agent_token, db_session):
    agent_id, token = agent_token
    resp = _call_work(client, auth_headers)
    assert resp.status_code == 200

    db_session.expire_all()
    agent = _agent_row(db_session, agent_id)
    assert agent.last_api_seen_at is not None
    assert agent.last_waker_poll_at is None


def test_waker_marked_call_refreshes_both(client, auth_headers, agent_token, db_session):
    agent_id, token = agent_token
    resp = _call_work(client, auth_headers, waker=True)
    assert resp.status_code == 200

    db_session.expire_all()
    agent = _agent_row(db_session, agent_id)
    assert agent.last_api_seen_at is not None
    assert agent.last_waker_poll_at is not None


def test_status_shape_waker_heartbeats(client, auth_headers, agent_token):
    _call_work(client, auth_headers, waker=True)

    resp = client.get("/api/v1/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    rows = body["waker_heartbeats"]
    assert isinstance(rows, list)
    assert rows, "in-project agent should appear in waker_heartbeats"
    # The auth'd agent has heartbeated in this test -> its row exists.
    row_by_id = {r["agent_id"]: r for r in rows}
    row = row_by_id[agent_token[0]]
    for field in ("agent_id", "agent_name", "persona", "last_waker_poll_at", "stale"):
        assert field in row, f"row missing {field}"
    assert row["stale"] is False  # fresh heartbeat within threshold


def _flag_for_timestamp(last: datetime | None, cut_minutes: int):
    """Compute the ``stale`` flag for one agent without an HTTP round-trip."""
    from server.services.status_service import build_waker_heartbeats

    class _FakeAgent:
        def __init__(self):
            self.id = "11111111-1111-4111-8111-111111111111"
            self.name = "test-agent"
            self.persona = "host"
            self.last_waker_poll_at = last

        def __getattr__(self, item):
            return None

    class _FakeDB:
        def scalars(self, stmt):
            class _Result:
                def __iter__(self):
                    return iter([_FakeAgent()])

            return _Result()

    now = datetime.now(timezone.utc)
    rows = build_waker_heartbeats(
        _FakeDB(), threshold_minutes=cut_minutes, now=now
    )
    assert len(rows) == 1
    return rows[0]


def test_null_never_not_stale():
    row = _flag_for_timestamp(None, cut_minutes=15)
    assert row.last_waker_poll_at is None
    assert row.stale is False


def test_fresh_heartbeat_not_stale():
    now = datetime.now(timezone.utc)
    row = _flag_for_timestamp(now - timedelta(minutes=5), cut_minutes=15)
    assert row.stale is False


def test_stale_when_older_than_threshold():
    now = datetime.now(timezone.utc)
    row = _flag_for_timestamp(now - timedelta(minutes=30), cut_minutes=15)
    assert row.stale is True


def test_boundary_exact_threshold_is_stale():
    # "older than threshold" -> equal-age heartbeat is stale (strict < cutoff).
    now = datetime.now(timezone.utc)
    row = _flag_for_timestamp(now - timedelta(minutes=15), cut_minutes=15)
    assert row.stale is True
