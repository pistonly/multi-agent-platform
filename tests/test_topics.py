"""Topic domain tests (post v0.13 M58).

M58 retired all DB topic write endpoints (HTTP 410 / CLI exit 2). This module
now covers:
- (a) retired write endpoints → uniform 410 + guidance body (parameterized)
- (b) consumer read paths (list / detail / todos / names) → DB-direct fixtures
- (c) experiment-domain linkage → topics inserted via ORM, experiments still
  created over HTTP (experiment writes are NOT retired)

Write-behavior tests (state machine, permissions, ack gating, resolve upsert)
were removed together with the retired endpoints; their FS equivalents live in
tests/test_fs_source.py.
"""

import uuid

import pytest
from map_types.enums import TopicStatus
from sqlalchemy import select

from server.domain.models import Agent, TopicComment
from tests._db_topic_factory import db_add_comment, db_create_topic
from tests._frontmatter import make_valid_plan

pytestmark = pytest.mark.slow


def _agent_by_name(db, name):
    return db.scalar(select(Agent).where(Agent.name == name))


def _host_agent(db):
    return _agent_by_name(db, "test-agent")


def _reviewer_agent(db):
    return _agent_by_name(db, "reviewer-agent")


def _admin_agent(db):
    return _agent_by_name(db, "admin-agent")


def _make_topic(db, project, *, creator=None, **overrides):
    return db_create_topic(
        db,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=(creator or _host_agent(db)).id,
        **overrides,
    )


# ---------------------------------------------------------------------------
# (a) retired write endpoints → 410 with guidance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path_template", "body"),
    [
        ("post", "/api/v1/projects/{pid}/topics", {"title": "t"}),
        ("post", "/api/v1/topics/{tid}/close", {"close_reason": "x"}),
        ("post", "/api/v1/topics/{tid}/reopen", None),
        ("post", "/api/v1/topics/{tid}/advance-round", {}),
        ("post", "/api/v1/topics/{tid}/rollback-round", None),
        ("post", "/api/v1/topics/{tid}/resolve", {"decision": "d"}),
        ("post", "/api/v1/topics/{tid}/comments", {"body": "c"}),
        ("delete", "/api/v1/topics/{tid}", None),
        ("patch", "/api/v1/topics/{tid}", {"title": "x"}),
        ("patch", "/api/v1/topics/{tid}", {"pinned": True}),
        ("patch", "/api/v1/topics/{tid}", {"description": "x"}),
    ],
)
def test_topic_write_endpoints_retired_410(
    client, auth_headers, project, method, path_template, body
):
    tid = "00000000-0000-0000-0000-000000000001"
    url = path_template.format(pid=project["id"], tid=tid)
    if method == "delete":
        resp = client.delete(url, headers=auth_headers)
    else:
        resp = getattr(client, method)(url, headers=auth_headers, json=body)
    assert resp.status_code == 410, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "topic_write_retired"
    assert "v0.13 M58" in detail["message"]
    assert detail["hint"], "410 body must carry an actionable hint"


def test_topic_patch_archived_subfield_survives_m58(client, db_session, auth_headers, project):
    """PATCH 仅保留 archived 子字段（topic migrate 收尾归档依赖）。"""
    topic = _make_topic(db_session, project)
    archived = client.patch(
        f"/api/v1/topics/{topic.id}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None

    restored = client.patch(
        f"/api/v1/topics/{topic.id}",
        headers=auth_headers,
        json={"archived": False},
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


# ---------------------------------------------------------------------------
# (b) consumer read paths
# ---------------------------------------------------------------------------


def test_topic_read_paths_after_m58(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project)

    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    detail = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["experiments"] == []
    assert detail.json()["status"] == "open"
    assert detail.json()["discussion_round"] == "round1"


def test_topic_list_includes_last_comment_author(client, db_session, auth_headers, reviewer, project):
    topic = _make_topic(db_session, project)
    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert listing.json()[0]["last_comment_author_agent_id"] is None

    db_add_comment(
        db_session, topic_id=topic.id, author=_host_agent(db_session), body="host 开场"
    )
    db_add_comment(
        db_session, topic_id=topic.id, author=_reviewer_agent(db_session), body="reviewer 插话"
    )

    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    row = next(item for item in listing.json() if item["id"] == str(topic.id))
    assert row["last_comment_author_agent_id"] == reviewer["id"]
    assert row["comment_count"] == 2
    assert row["last_comment_id"] is not None
    assert row["last_comment_author_name"] == "reviewer-agent"
    assert row["last_comment_excerpt"] == "reviewer 插话"
    assert row["my_comment_count"] == 1


def test_topic_status_filter(client, db_session, auth_headers, project):
    _make_topic(db_session, project, title="第一个")
    _make_topic(db_session, project, title="第二个", status=TopicStatus.closed)

    opened = client.get(f"/api/v1/projects/{project['id']}/topics?status=open", headers=auth_headers)
    assert len(opened.json()) == 1
    closed_only = client.get(
        f"/api/v1/projects/{project['id']}/topics?status=closed", headers=auth_headers
    )
    assert len(closed_only.json()) == 1


def test_topic_comments_tree(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project)
    host = _host_agent(db_session)
    parent = db_add_comment(db_session, topic_id=topic.id, author=host, body="顶层评论")
    db_add_comment(
        db_session, topic_id=topic.id, author=host, body="回复", parent_id=parent.id
    )

    tree = client.get(f"/api/v1/topics/{topic.id}/comments?tree=true", headers=auth_headers)
    assert tree.status_code == 200
    nodes = tree.json()
    assert len(nodes) == 1
    assert nodes[0]["body"] == "顶层评论"
    assert len(nodes[0]["children"]) == 1
    assert nodes[0]["children"][0]["body"] == "回复"

    detail = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert detail.json()["comment_count"] == 2


def test_topic_detail_backfills_legacy_null_comment_seq(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project)
    host = _host_agent(db_session)
    first = db_add_comment(db_session, topic_id=topic.id, author=host, body="legacy top-level")
    db_add_comment(db_session, topic_id=topic.id, author=host, body="legacy reply", parent_id=first.id)

    rows = list(
        db_session.scalars(
            select(TopicComment)
            .where(TopicComment.topic_id == topic.id)
            .order_by(TopicComment.created_at.asc(), TopicComment.id.asc())
        )
    )
    rows[0].comment_seq = None
    rows[1].comment_seq = None

    detail = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    root = detail.json()["comments"][0]
    seqs = [root["comment_seq"], root["children"][0]["comment_seq"]]
    assert sorted(seqs) == [1, 2]


def test_round_summary_comment_sets_pending_ack_todos(client, db_session, auth_headers, reviewer, project):
    """消费者路径：Round Summary 落库后 todos 的 pending_round_acks 读路径。

    写触发（DB comment / advance ack）已退役，此处经 service 层直插评论，
    保留「存量话题的 pending_round_acks 投影」读语义。
    """
    topic = _make_topic(db_session, project)
    db_add_comment(
        db_session, topic_id=topic.id, author=_reviewer_agent(db_session), body="participant opinion"
    )
    summary = db_add_comment(
        db_session,
        topic_id=topic.id,
        author=_host_agent(db_session),
        body="## Round 1 Summary\n\n### 已共识\n- 混合方案\n",
    )

    detail = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert detail.json()["advance_round_pending_since"] is not None

    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert len(reviewer_todos["pending_round_acks"]) == 1
    assert reviewer_todos["pending_round_acks"][0]["topic_id"] == str(topic.id)
    assert reviewer_todos["pending_round_acks"][0]["summary_comment_id"] == str(summary.id)


def test_project_status_includes_open_topics(client, db_session, auth_headers, project):
    open_topic = _make_topic(db_session, project, title="进行中的讨论")
    _make_topic(db_session, project, title="已关闭的讨论", status=TopicStatus.closed)

    status = client.get(f"/api/v1/projects/{project['id']}/status", headers=auth_headers)
    assert status.status_code == 200
    body = status.json()
    assert "open_topics" in body
    open_ids = {t["id"] for t in body["open_topics"]}
    assert str(open_topic.id) in open_ids
    match = next(t for t in body["open_topics"] if t["id"] == str(open_topic.id))
    assert match["title"] == "进行中的讨论"
    assert match["status"] == "open"
    assert match["creator_name"] == "test-agent"


def test_topic_shows_creator_and_comment_author_names(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project, title="作者展示测试")
    db_add_comment(
        db_session, topic_id=topic.id, author=_host_agent(db_session), body="一条评论"
    )

    detail = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert detail.json()["creator_name"] == "test-agent"
    assert detail.json()["comments"][0]["author_name"] == "test-agent"


def test_topic_archive_hidden_by_default(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project)
    archived = client.patch(
        f"/api/v1/topics/{topic.id}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json() == []

    with_archived = client.get(
        f"/api/v1/projects/{project['id']}/topics?include_archived=true",
        headers=auth_headers,
    )
    assert len(with_archived.json()) == 1

    restored = client.patch(
        f"/api/v1/topics/{topic.id}",
        headers=auth_headers,
        json={"archived": False},
    )
    assert restored.json()["archived_at"] is None


# ---------------------------------------------------------------------------
# (c) experiment-domain linkage (topic fixtures via ORM, experiments via HTTP)
# ---------------------------------------------------------------------------


def test_experiment_linked_to_topic(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project)

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "正式实验",
            "plan": {"content_md": make_valid_plan(body="plan")},
            "submit_for_review": False,
            "topic_id": str(topic.id),
        },
    )
    assert exp.status_code == 201
    assert exp.json()["topic_id"] == str(topic.id)
    exp_id = exp.json()["id"]

    exp_detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
    assert exp_detail.status_code == 200
    assert exp_detail.json()["topic_id"] == str(topic.id)

    bundle = client.get(f"/api/v1/experiments/{exp_id}/bundle", headers=auth_headers)
    assert bundle.status_code == 200
    assert bundle.json()["experiment"]["topic_id"] == str(topic.id)

    detail = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert detail.json()["experiment_count"] == 1
    assert detail.json()["experiments"][0]["id"] == exp.json()["id"]


def test_topic_cross_project_isolation(client, db_session, auth_headers, project, admin_headers):
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "other-proj", "name": "Other", "workspace_path": "/tmp/other"},
    )
    assert other.status_code == 201
    other_topic = _make_topic(
        db_session, other.json(), creator=_admin_agent(db_session), title="别的项目话题"
    )

    forbidden = client.get(f"/api/v1/topics/{other_topic.id}", headers=auth_headers)
    assert forbidden.status_code == 403

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "x",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(other_topic.id),
        },
    )
    assert resp.status_code == 404


def test_cannot_create_second_active_experiment_on_topic(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project)
    first = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "唯一活跃实验",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "重复实验",
            "plan": {"content_md": make_valid_plan(body="p2")},
            "topic_id": str(topic.id),
        },
    )
    assert second.status_code == 409

    client.post(f"/api/v1/experiments/{first.json()['id']}/cancel", headers=auth_headers)
    third = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "取消后可再建",
            "plan": {"content_md": make_valid_plan(body="p3")},
            "topic_id": str(topic.id),
        },
    )
    assert third.status_code == 201


def test_cannot_create_experiment_on_closed_topic(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project, status=TopicStatus.closed)

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "关闭后实验",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"].lower()


def test_only_topic_host_can_create_experiment_from_topic(
    client, db_session, auth_headers, reviewer, project
):
    topic = _make_topic(db_session, project)

    denied = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=reviewer["headers"],
        json={
            "title": "非主持抢开",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert denied.status_code == 403
    assert "host" in denied.json()["detail"].lower()

    allowed = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "主持开实验",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert allowed.status_code == 201
    assert allowed.json()["warnings"] == ["topic_not_ready_for_experiment"]


def test_ready_topic_create_experiment_has_no_not_ready_warning(
    client, db_session, auth_headers, project
):
    topic = _make_topic(db_session, project, discussion_round="ready")

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "ready 后开实验",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert resp.status_code == 201
    assert resp.json()["warnings"] == []


def test_admin_can_create_experiment_from_others_topic(
    client, db_session, admin_headers, auth_headers, project
):
    topic = _make_topic(db_session, project, title="他人主持的话题")

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={
            "title": "管理员代开",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert resp.status_code == 201
    assert resp.json()["topic_id"] == str(topic.id)
    assert resp.json()["creator_agent_id"] != str(topic.creator_agent_id)


def test_experiment_archive_rejects_active_phase(client, db_session, auth_headers, project):
    topic = _make_topic(db_session, project, title="归档后重开实验")
    first = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "第一个",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert first.status_code == 201
    exp_id = first.json()["id"]

    archived = client.patch(
        f"/api/v1/experiments/{exp_id}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 409
    assert "complete or cancel" in archived.json()["detail"]

    second = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "第二个",
            "plan": {"content_md": make_valid_plan(body="p2")},
            "topic_id": str(topic.id),
        },
    )
    assert second.status_code == 409, second.text


def test_experiment_archive_allows_new_active_on_topic_after_cancelled(
    client, db_session, auth_headers, project
):
    topic = _make_topic(db_session, project, title="取消归档后重开实验")
    first = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "第一个",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    )
    assert first.status_code == 201
    exp_id = first.json()["id"]
    cancelled = client.post(f"/api/v1/experiments/{exp_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200

    archived = client.patch(
        f"/api/v1/experiments/{exp_id}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    second = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "第二个",
            "plan": {"content_md": make_valid_plan(body="p2")},
            "topic_id": str(topic.id),
        },
    )
    assert second.status_code == 201, second.text
