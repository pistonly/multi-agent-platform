from typing import Any

from cli.participant_worker import ParticipantMapClient, ParticipantWorker, ParticipantWorkerConfig


class FakeParticipantClient(ParticipantMapClient):
    def __init__(
        self,
        *,
        agent_id: str = "participant-agent",
        open_topics: list[dict[str, Any]] | None = None,
        topics: dict[str, dict[str, Any]] | None = None,
        todos: dict[str, Any] | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._open_topics = open_topics or []
        self._topics = topics or {}
        self._todos = todos or {}
        self.comments: list[dict[str, Any]] = []

    def whoami(self) -> dict[str, Any]:
        return {"id": self._agent_id, "name": "participant"}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def topic_list_open(self) -> list[dict[str, Any]]:
        return self._open_topics

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        return self._topics[topic_id]

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any]:
        comment = {"topic_id": topic_id, "body": body, "parent_id": parent_id}
        self.comments.append(comment)
        return comment


def test_participant_initial_comment_on_open_topic(tmp_path):
    client = FakeParticipantClient(
        open_topics=[{"id": "topic-1", "title": "新话题", "status": "open"}],
        topics={
            "topic-1": {
                "id": "topic-1",
                "title": "新话题",
                "description": "讨论自动化",
                "status": "open",
                "creator_agent_id": "host-agent",
                "comments": [],
                "experiments": [],
            }
        },
    )
    stats = ParticipantWorker(
        client,
        ParticipantWorkerConfig(once=True, state_file=tmp_path / "state.json"),
    ).run_once()
    assert stats.comments_created == 1
    assert client.comments[0]["topic_id"] == "topic-1"
    assert "新话题" in client.comments[0]["body"]


def test_participant_skips_own_topics(tmp_path):
    client = FakeParticipantClient(
        agent_id="host-agent",
        open_topics=[{"id": "topic-own", "title": "我的话题"}],
        topics={
            "topic-own": {
                "id": "topic-own",
                "status": "open",
                "creator_agent_id": "host-agent",
                "comments": [],
                "experiments": [],
            }
        },
    )
    stats = ParticipantWorker(
        client,
        ParticipantWorkerConfig(once=True, state_file=tmp_path / "state.json"),
    ).run_once()
    assert stats.comments_created == 0
    assert stats.opportunities_seen == 0


def test_participant_follow_up_after_host_comment(tmp_path):
    state_file = tmp_path / "participant-state.json"
    client = FakeParticipantClient(
        open_topics=[{"id": "topic-1"}],
        topics={
            "topic-1": {
                "id": "topic-1",
                "title": "跟进",
                "status": "open",
                "creator_agent_id": "host-agent",
                "experiments": [],
                "comments": [
                    {
                        "id": "c1",
                        "author_agent_id": "participant-agent",
                        "body": "首轮观点",
                        "created_at": "2026-06-29T10:00:00",
                        "children": [],
                    },
                    {
                        "id": "c2",
                        "author_agent_id": "host-agent",
                        "author_name": "host",
                        "body": "## Round 1 Summary",
                        "created_at": "2026-06-29T11:00:00",
                        "children": [],
                    },
                ],
            }
        },
    )
    stats = ParticipantWorker(
        client,
        ParticipantWorkerConfig(once=True, state_file=state_file),
    ).run_once()
    assert stats.comments_created == 1
    assert client.comments[0]["parent_id"] == "c2"


def test_participant_pauses_until_host_round1_summary(tmp_path):
    """After enough Round 1 comments, stop ping-pong until host posts Round 1 Summary."""
    client = FakeParticipantClient(
        agent_id="participant-agent",
        open_topics=[{"id": "topic-1"}],
        todos={
            "mentions": [
                {
                    "id": "mention-1",
                    "topic_id": "topic-1",
                    "source_id": "host-new",
                    "excerpt": "@participant please confirm",
                    "author_name": "host",
                }
            ]
        },
        topics={
            "topic-1": {
                "id": "topic-1",
                "status": "open",
                "discussion_round": "round1",
                "round_summary_count": 0,
                "creator_agent_id": "host-agent",
                "experiments": [],
                "comments": [
                    {
                        "id": "p1",
                        "author_agent_id": "participant-agent",
                        "body": "first",
                        "created_at": "2026-06-29T10:00:00",
                        "children": [],
                    },
                    {
                        "id": "p2",
                        "author_agent_id": "participant-agent",
                        "body": "second",
                        "created_at": "2026-06-29T10:01:00",
                        "children": [],
                    },
                    {
                        "id": "host-new",
                        "author_agent_id": "host-agent",
                        "body": "Thanks @participant",
                        "created_at": "2026-06-29T10:02:00",
                        "children": [],
                    },
                ],
            }
        },
    )
    stats = ParticipantWorker(
        client,
        ParticipantWorkerConfig(once=True, state_file=tmp_path / "state.json"),
    ).run_once()
    assert stats.comments_created == 0
    assert stats.opportunities_seen == 0


def test_participant_mention_replies_in_thread(tmp_path):
    client = FakeParticipantClient(
        open_topics=[{"id": "topic-1"}],
        todos={
            "mentions": [
                {
                    "id": "mention-1",
                    "topic_id": "topic-1",
                    "source_id": "host-c1",
                    "excerpt": "@participant thoughts?",
                    "author_name": "host",
                }
            ]
        },
        topics={
            "topic-1": {
                "id": "topic-1",
                "status": "open",
                "discussion_round": "round1",
                "round_summary_count": 0,
                "creator_agent_id": "host-agent",
                "experiments": [],
                "comments": [
                    {
                        "id": "host-c1",
                        "author_agent_id": "host-agent",
                        "body": "@participant thoughts?",
                        "created_at": "2026-06-29T10:00:00",
                        "children": [],
                    }
                ],
            }
        },
    )
    stats = ParticipantWorker(
        client,
        ParticipantWorkerConfig(once=True, state_file=tmp_path / "state.json"),
    ).run_once()
    assert stats.comments_created == 1
    assert client.comments[0]["parent_id"] == "host-c1"


def test_participant_idempotent_trigger(tmp_path):
    state_file = tmp_path / "participant-state.json"
    state_file.write_text(
        '{"schema_version":1,"topics":{"topic-1":{"last_handled_trigger_id":"initial:topic-1"}}}',
        encoding="utf-8",
    )
    client = FakeParticipantClient(
        open_topics=[{"id": "topic-1"}],
        topics={
            "topic-1": {
                "id": "topic-1",
                "status": "open",
                "creator_agent_id": "host-agent",
                "comments": [],
                "experiments": [],
            }
        },
    )
    stats = ParticipantWorker(
        client,
        ParticipantWorkerConfig(once=True, state_file=state_file),
    ).run_once()
    assert stats.comments_created == 0
    assert stats.opportunities_seen == 0
