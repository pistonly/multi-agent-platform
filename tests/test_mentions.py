"""Mention lifecycle tests (M58b-3 recategorised).

v0.13 M58 retired the DB topic write endpoints (HTTP 410), so mention
coverage follows the experiment's assertion-semantics classes:

* experiment-comment production stays the primary live verification
  surface (comment_service still writes the Mention table);
* consumer semantics (dismiss / auto-dismiss / stale projection /
  notification cascade) are preserved and driven through experiment
  comments;
* a topic-source fixture remains only where the assertion targets a
  retained consumer projection (topic-progress obligation), built via
  DB-direct factories;
* topic-domain production assertions and round-ack dismissal chains
  died with the 410 endpoints and were removed — the 410 behaviour is
  centrally covered by test_topics.py.
"""

from pathlib import Path

import uuid

import pytest
from sqlalchemy import select

from server.domain.models import Agent, Mention
from tests._db_topic_factory import db_add_comment, db_create_topic
from tests._frontmatter import make_valid_plan

pytestmark = pytest.mark.slow

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _mention_experiment(client, auth_headers, project, title="Mention exp"):
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": title, "plan": {"content_md": make_valid_plan(body="# p")}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()
    return exp, plan


def _exp_comment(client, headers, exp_id, plan_id, body, parent_id=None):
    payload = {"anchor_type": "plan", "anchor_id": plan_id, "body": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    return client.post(
        f"/api/v1/experiments/{exp_id}/comments",
        headers=headers,
        json=payload,
    )


def _backdate_exp_comments(db_session, exp_id, *, seconds=5):
    """Experiment comments carry no comment_seq and SQLite CURRENT_TIMESTAMP
    has second precision, so same-second ordering degenerates to a random
    UUID tie-break in thread_activity.comment_after. Backdate the mention
    source comments so later participation is unambiguously after them."""
    from datetime import timedelta

    from server.domain.models import Comment

    for row in db_session.scalars(
        select(Comment).where(Comment.experiment_id == uuid.UUID(exp_id))
    ):
        row.created_at = row.created_at - timedelta(seconds=seconds)
    db_session.commit()


def test_mention_in_experiment_comment_creates_todo_and_notification(
    client, auth_headers, reviewer, project
):
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project)

    _exp_comment(client, auth_headers, exp["id"], plan["id"], "请 @reviewer-agent 看一下这个计划")

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) >= 1
    mention = todos["mentions"][0]
    assert mention["experiment_id"] == exp["id"]
    assert "reviewer-agent" in mention["excerpt"] or "看一下" in mention["excerpt"]

    notifs = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers).json()
    assert any(n["event"] == "agent.mentioned" for n in notifs["items"])


def test_self_mention_ignored(client, auth_headers, project):
    exp, plan = _mention_experiment(client, auth_headers, project, "Self")
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()

    _exp_comment(client, auth_headers, exp["id"], plan["id"], f"@{me['name']} 自言自语")

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert not any(m["author_agent_id"] == me["id"] for m in todos["mentions"])


def test_unknown_mention_soft_warns_author(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project, "Unknown")

    resp = _exp_comment(client, auth_headers, exp["id"], plan["id"], "@no-such-agent hello")
    assert resp.status_code == 201
    assert resp.json()["unresolved_mentions"] == ["no-such-agent"]

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []

    notifs = client.get("/api/v1/agents/me/notifications", headers=auth_headers).json()
    assert any(n["event"] == "mention.unresolved" for n in notifs["items"])


def test_mention_inside_inline_code_ignored(client, auth_headers, reviewer, project):
    exp, plan = _mention_experiment(client, auth_headers, project, "Code mention")
    resp = _exp_comment(
        client,
        auth_headers,
        exp["id"],
        plan["id"],
        "工具 `` `pytest` `` 与 `` `@host` `` 不应触发 mention",
    )
    assert resp.status_code == 201
    assert resp.json()["unresolved_mentions"] == []

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert not any(m["experiment_id"] == exp["id"] for m in todos["mentions"])


def test_mention_inside_fenced_code_ignored(client, auth_headers, reviewer, project):
    exp, plan = _mention_experiment(client, auth_headers, project, "Fenced code")
    body = (
        "示例：\n```\n"
        "@multi-agent-platform-host\n"
        "`@host`\n"
        "```\n"
        "块外请 @reviewer-agent 参与"
    )
    resp = _exp_comment(client, auth_headers, exp["id"], plan["id"], body)
    assert resp.status_code == 201
    assert resp.json()["unresolved_mentions"] == []

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert any(m["experiment_id"] == exp["id"] for m in todos["mentions"])


def test_golden_comment_seq_4_fixture_unresolved_empty(client, auth_headers, project):
    exp, plan = _mention_experiment(client, auth_headers, project, "Golden seq4")
    body = (_FIXTURES / "mention_comment_seq_4.md").read_text(encoding="utf-8")
    resp = _exp_comment(client, auth_headers, exp["id"], plan["id"], body)
    assert resp.status_code == 201
    assert resp.json()["unresolved_mentions"] == []


def test_golden_comment_seq_5_fixture_unresolved_empty(client, auth_headers, project):
    exp, plan = _mention_experiment(client, auth_headers, project, "Golden seq5")
    body = (_FIXTURES / "mention_comment_seq_5.md").read_text(encoding="utf-8")
    resp = _exp_comment(client, auth_headers, exp["id"], plan["id"], body)
    assert resp.status_code == 201
    assert resp.json()["unresolved_mentions"] == []


def test_dismiss_single_mention(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project, "Dismiss one")
    _exp_comment(client, auth_headers, exp["id"], plan["id"], "@reviewer-agent 看一眼")

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 1
    mention_id = todos["mentions"][0]["id"]
    assert todos["mentions"][0]["dismissed_at"] is None

    resp = client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=reviewer_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == mention_id
    assert body["dismissed_at"] is not None

    todos_after = client.get(
        "/api/v1/agents/me/todos", headers=reviewer_headers
    ).json()
    assert todos_after["mentions"] == []

    # idempotent: dismissing twice still 200
    resp2 = client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=reviewer_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["dismissed_at"] == body["dismissed_at"]


def test_dismiss_all_mentions(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project, "Dismiss all")
    for body in [
        "@reviewer-agent first",
        "@reviewer-agent second",
        "no mention here",
    ]:
        _exp_comment(client, auth_headers, exp["id"], plan["id"], body)

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 2

    resp = client.post("/api/v1/agents/me/mentions/dismiss-all", headers=reviewer_headers)
    assert resp.status_code == 200
    assert resp.json()["dismissed"] == 2

    todos_after = client.get(
        "/api/v1/agents/me/todos", headers=reviewer_headers
    ).json()
    assert todos_after["mentions"] == []


def test_dismiss_other_agents_mention_forbidden(
    client, auth_headers, reviewer, project
):
    """A mention addressed to someone else must not be dismissable by me."""
    exp, plan = _mention_experiment(client, auth_headers, project, "Forbidden")
    _exp_comment(client, auth_headers, exp["id"], plan["id"], "@reviewer-agent ping")

    # `auth_headers` belongs to a different agent; trying to dismiss
    # reviewer's mention should 404 (we hide existence behind not-found).
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert len(todos["mentions"]) == 1
    mention_id = todos["mentions"][0]["id"]

    resp = client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=auth_headers,
    )
    assert resp.status_code == 404

    # And reviewer can still see it untouched
    todos_after = client.get(
        "/api/v1/agents/me/todos", headers=reviewer["headers"]
    ).json()
    assert len(todos_after["mentions"]) == 1
    assert todos_after["mentions"][0]["dismissed_at"] is None


def test_dismiss_mention_cascades_notification_read(
    client, auth_headers, reviewer, project
):
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project, "dismiss-cascade")
    _exp_comment(client, auth_headers, exp["id"], plan["id"], "@reviewer-agent cascade test")
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    mention_id = todos["mentions"][0]["id"]

    notifs_before = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"unread_only": True, "limit": 50},
    ).json()
    assert any(n["event"] == "agent.mentioned" for n in notifs_before["items"])

    client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=reviewer_headers,
    )

    todos_after = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos_after["mentions"] == []
    notifs_after = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"unread_only": True, "limit": 50},
    ).json()
    assert not any(
        n["event"] == "agent.mentioned" and n.get("read_at") is None
        for n in notifs_after["items"]
    )


def test_auto_dismiss_on_reply_in_experiment_thread(
    client, auth_headers, reviewer, project
):
    """When the mentioned agent posts a reply in the same experiment thread,
    any prior @-mentions of him in that thread auto-dismiss."""
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project, "Auto dismiss")

    root = _exp_comment(
        client, auth_headers, exp["id"], plan["id"], "Plan note @reviewer-agent 请看"
    ).json()
    _exp_comment(
        client, reviewer_headers, exp["id"], plan["id"], "已读", parent_id=root["id"]
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == [], (
        "reviewer's mention should auto-dismiss after he replied in the thread"
    )


def test_auto_dismiss_does_not_touch_other_experiment(
    client, auth_headers, reviewer, project, db_session
):
    """Container auto-dismiss is per experiment — commenting on exp A must not clear exp B."""
    reviewer_headers = reviewer["headers"]
    exp_a, plan_a = _mention_experiment(client, auth_headers, project, "Exp A")
    exp_b, plan_b = _mention_experiment(client, auth_headers, project, "Exp B")

    _exp_comment(client, auth_headers, exp_a["id"], plan_a["id"], "Exp A @reviewer-agent 看 a")
    _exp_comment(client, auth_headers, exp_b["id"], plan_b["id"], "Exp B @reviewer-agent 看 b")
    _backdate_exp_comments(db_session, exp_a["id"])
    _backdate_exp_comments(db_session, exp_b["id"])
    _exp_comment(client, reviewer_headers, exp_a["id"], plan_a["id"], "回复 A")

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 1
    assert todos["mentions"][0]["experiment_id"] == exp_b["id"]


def test_auto_dismiss_experiment_on_top_level_comment(
    client, auth_headers, reviewer, project, db_session
):
    """Top-level comment after @mentions dismisses them via participation rules (T1 D2)."""
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project, "Top-level reply")

    _exp_comment(client, auth_headers, exp["id"], plan["id"], "Thread A @reviewer-agent 看 a")
    _exp_comment(client, auth_headers, exp["id"], plan["id"], "Thread B @reviewer-agent 看 b")
    _backdate_exp_comments(db_session, exp["id"])
    _exp_comment(
        client, reviewer_headers, exp["id"], plan["id"], "新顶层回复，未挂 parent"
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []


def test_stale_mention_filtered_in_todos_without_read_write(
    client, auth_headers, reviewer, project, db_session
):
    """get_todos must not write; stale mentions are filtered in projection only."""
    reviewer_headers = reviewer["headers"]
    exp, plan = _mention_experiment(client, auth_headers, project, "Stale projection")
    root = _exp_comment(
        client, auth_headers, exp["id"], plan["id"], "@reviewer-agent stale mention"
    ).json()
    _exp_comment(
        client,
        reviewer_headers,
        exp["id"],
        plan["id"],
        "already replied",
        parent_id=root["id"],
    )

    mention = db_session.scalar(
        select(Mention).where(
            Mention.mentioned_agent_id == uuid.UUID(reviewer["id"]),
            Mention.source_id == uuid.UUID(root["id"]),
        )
    )
    assert mention is not None
    assert mention.dismissed_at is not None

    mention.dismissed_at = None
    db_session.commit()

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []
    db_session.refresh(mention)
    assert mention.dismissed_at is None


def test_mention_read_notification_does_not_clear_obligation(
    client, auth_headers, reviewer, project, db_session
):
    """Reading agent.mentioned notification must not dismiss mention obligation (T2 PR2 c)."""
    host = db_session.scalar(select(Agent).where(Agent.name == "test-agent"))
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=host.id,
        title="read-notif-only",
        description=None,
    )
    db_add_comment(
        db_session, topic_id=topic.id, author=host, body="@reviewer-agent please review"
    )
    tid = str(topic.id)

    reviewer_headers = reviewer["headers"]
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len([m for m in todos["mentions"] if m["topic_id"] == tid]) == 1
    progress_before = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer_headers
    ).json()

    def _obligation_mentions(progress):
        return [
            w
            for item in progress.get("items", [])
            if item["topic_id"] == tid
            for w in item.get("work_items", [])
            if w.get("kind") == "mention" and w.get("priority") == "obligation"
        ]

    assert len(_obligation_mentions(progress_before)) == 1

    notifs = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"unread_only": True},
    ).json()
    mentioned = [
        n
        for n in notifs["items"]
        if n["event"] == "agent.mentioned" and n.get("read_at") is None
    ]
    assert len(mentioned) >= 1
    notif_id = mentioned[0]["id"]

    read_resp = client.post(
        f"/api/v1/notifications/{notif_id}/read",
        headers=reviewer_headers,
    )
    assert read_resp.status_code == 200

    todos_after = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len([m for m in todos_after["mentions"] if m["topic_id"] == tid]) == 1
    progress_after = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer_headers
    ).json()
    assert len(_obligation_mentions(progress_after)) == 1
