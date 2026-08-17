"""v0.13 M58: topic-comment notification production tests were removed — the
DB comment write endpoint is retired (410), so the comment→notification
route has no production caller left. Consumers (waker notification bucket,
Web notifications page) are covered elsewhere. What stays here are the
experiment-domain creation warnings, whose topic fixtures are now DB-direct
inserts.
"""

import uuid

from sqlalchemy import select

from server.domain.models import Agent
from tests._db_topic_factory import db_create_topic
from tests._frontmatter import make_valid_plan


def _host(db):
    return db.scalar(select(Agent).where(Agent.name == "test-agent"))


def test_create_experiment_no_topic_id_warning(client, db_session, auth_headers, project):
    db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_host(db_session).id,
        title="open 话题",
        description=None,
    )

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "无话题实验", "plan": {"content_md": make_valid_plan(body="p")}},
    ).json()
    assert exp["warnings"] == ["no_topic_id"]


def test_create_experiment_with_not_ready_topic_id_warning(client, db_session, auth_headers, project):
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_host(db_session).id,
        title="绑定话题",
        description=None,
    )

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "有话题实验",
            "plan": {"content_md": make_valid_plan(body="p")},
            "topic_id": str(topic.id),
        },
    ).json()
    assert exp["warnings"] == ["topic_not_ready_for_experiment"]
