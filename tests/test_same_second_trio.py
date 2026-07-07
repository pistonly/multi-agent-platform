"""T3 D5 / T3-4: same-second comment trio — seq monotonic + obligation projection."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from server.domain.models import TopicComment

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "same_second_trio.yaml"


def _load_fixture() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _create_topic(client, headers, project, spec: dict):
    resp = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=headers,
        json={"title": spec["title"], "description": spec.get("description", "")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_comment(client, headers, topic_id, body: str, parent_id: str | None = None):
    payload: dict = {"body": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    resp = client.post(
        f"/api/v1/topics/{topic_id}/comments",
        headers=headers,
        json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _obligation_kinds(progress: dict) -> set[str]:
    kinds: set[str] = set()
    for item in progress.get("items", []):
        for work in item.get("work_items", []):
            if work.get("priority") == "obligation":
                kinds.add(work["kind"])
    return kinds


def test_same_second_trio_seq_and_obligations(
    client, auth_headers, project, reviewer, db_session
):
    """Same created_at for summary + reply + mention; comment_seq stays strict."""
    spec = _load_fixture()
    topic = _create_topic(client, auth_headers, project, spec["topic"])
    tid = topic["id"]
    participant_headers = reviewer["headers"]

    preface = spec["preface"][0]
    root = _post_comment(
        client,
        participant_headers,
        tid,
        preface["body"],
    )

    same_second_comments: list[dict] = []
    for step in spec["same_second"]:
        headers = auth_headers if step["role"] == "host" else participant_headers
        parent_id = root["id"] if step.get("parent") == "preface" else None
        same_second_comments.append(
            _post_comment(client, headers, tid, step["body"].strip(), parent_id=parent_id)
        )

    stamp = datetime(2026, 7, 4, 12, 0, 0)
    comment_ids = [uuid.UUID(c["id"]) for c in same_second_comments]
    all_topic_comment_ids = [uuid.UUID(c["id"]) for c in [root, *same_second_comments]]
    for row in db_session.scalars(
        select(TopicComment).where(TopicComment.id.in_(all_topic_comment_ids))
    ):
        row.created_at = stamp
    db_session.commit()

    all_comments = list(
        db_session.scalars(
            select(TopicComment)
            .where(TopicComment.topic_id == uuid.UUID(tid))
            .order_by(TopicComment.comment_seq.asc())
        )
    )
    seqs = [c.comment_seq for c in all_comments]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    trio = [c for c in all_comments if c.id in comment_ids]
    assert len(trio) == 3
    assert all(c.created_at == stamp for c in trio)
    assert trio[0].comment_seq < trio[1].comment_seq < trio[2].comment_seq

    host_todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    host_progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    assert len(host_todos["pending_topic_replies"]) >= 1
    assert "pending_topic_reply" in _obligation_kinds(host_progress)

    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=participant_headers).json()
    reviewer_progress = client.get(
        "/api/v1/agents/me/topic-progress", headers=participant_headers
    ).json()
    assert len(reviewer_todos["pending_round_acks"]) >= 1
    assert len(reviewer_todos["mentions"]) >= 1
    kinds = _obligation_kinds(reviewer_progress)
    assert "round_ack" in kinds
    assert "mention" in kinds
