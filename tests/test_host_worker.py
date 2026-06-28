from pathlib import Path
from typing import Any

from cli.host_worker import HostWorker, WorkerConfig


class FakeMapClient:
    def __init__(self, *, todos: dict[str, Any], topics: dict[str, dict[str, Any]]) -> None:
        self._todos = todos
        self._topics = topics
        self.comments: list[dict[str, Any]] = []
        self.experiments: list[dict[str, Any]] = []

    def whoami(self) -> dict[str, Any]:
        return {"id": "host-agent", "name": "host"}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        return self._topics[topic_id]

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any]:
        comment = {"topic_id": topic_id, "body": body, "parent_id": parent_id}
        self.comments.append(comment)
        return comment

    def experiment_create(
        self,
        title: str,
        plan_file: Path,
        *,
        topic_id: str,
        submit_for_review: bool,
    ) -> dict[str, Any]:
        experiment = {
            "title": title,
            "topic_id": topic_id,
            "plan": plan_file.read_text(encoding="utf-8"),
            "submit_for_review": submit_for_review,
        }
        self.experiments.append(experiment)
        return experiment


def test_worker_replies_to_pending_topic_comments():
    client = FakeMapClient(
        todos={
            "pending_topic_replies": [
                {
                    "topic_id": "topic-1",
                    "topic_title": "自动主持",
                    "comment_id": "comment-1",
                    "thread_root_id": "comment-1",
                    "author_name": "participant",
                    "excerpt": "需要补充验收标准",
                }
            ],
            "my_open_topics": [],
        },
        topics={},
    )

    stats = HostWorker(client, WorkerConfig(once=True)).run_once()

    assert stats.replies_created == 1
    assert client.comments == [
        {
            "topic_id": "topic-1",
            "parent_id": "comment-1",
            "body": "已收到这个 thread 的反馈。\n\n- 评论摘要：需要补充验收标准\n- 处理状态：已纳入主持跟进；如需进入实验，我会在本话题完成必要讨论后从 host persona 创建关联实验。",
        }
    ]


def test_worker_promotes_ready_topic_to_experiment(tmp_path):
    topic = {
        "id": "topic-ready",
        "title": "完成自动主持闭环",
        "description": "让 host worker 把话题提升为实验",
        "discussion_round": "ready",
        "round_summary_count": 2,
        "experiments": [],
        "comments": [
            {
                "id": "c1",
                "author_agent_id": "participant-agent",
                "body": "我支持进入实验",
                "children": [],
            },
            {
                "id": "c2",
                "author_agent_id": "host-agent",
                "body": "## Round 1 Summary\n\n...",
                "children": [],
            },
            {
                "id": "c3",
                "author_agent_id": "host-agent",
                "body": "## Round 2 Summary\n\n...",
                "children": [],
            },
        ],
    }
    client = FakeMapClient(
        todos={"pending_topic_replies": [], "my_open_topics": [{"id": "topic-ready"}]},
        topics={"topic-ready": topic},
    )
    config = WorkerConfig(
        once=True,
        promote_ready_topics=True,
        submit_created_experiment_for_review=True,
        plan_dir=tmp_path,
    )

    stats = HostWorker(client, config).run_once()

    assert stats.experiments_created == 1
    assert client.experiments[0]["title"] == "实验：完成自动主持闭环"
    assert client.experiments[0]["topic_id"] == "topic-ready"
    assert client.experiments[0]["submit_for_review"] is True
    assert "topic_id: `topic-ready`" in client.experiments[0]["plan"]
    assert (tmp_path / "topic-topic-ready-plan.md").exists()


def test_worker_uses_discussion_round_before_regex():
    topic = {
        "id": "topic-not-ready",
        "title": "字段优先",
        "discussion_round": "round2",
        "round_summary_count": 1,
        "experiments": [],
        "comments": [
            {"id": "c1", "author_agent_id": "participant-agent", "body": "同意", "children": []},
            {"id": "c2", "author_agent_id": "host-agent", "body": "## Round 1 Summary", "children": []},
            {"id": "c3", "author_agent_id": "host-agent", "body": "## Round 2 Summary", "children": []},
        ],
    }
    client = FakeMapClient(
        todos={"pending_topic_replies": [], "my_open_topics": [{"id": "topic-not-ready"}]},
        topics={"topic-not-ready": topic},
    )

    stats = HostWorker(client, WorkerConfig(once=True, promote_ready_topics=True)).run_once()

    assert stats.experiments_created == 0
    assert client.experiments == []


def test_worker_does_not_promote_topic_with_active_experiment():
    topic = {
        "id": "topic-active",
        "title": "已有实验",
        "experiments": [{"id": "exp-1", "phase": "running", "archived_at": None}],
        "comments": [
            {"id": "c1", "author_agent_id": "participant-agent", "body": "同意", "children": []},
            {"id": "c2", "author_agent_id": "host-agent", "body": "Round 1 Summary", "children": []},
            {"id": "c3", "author_agent_id": "host-agent", "body": "Round 2 Summary", "children": []},
        ],
    }
    client = FakeMapClient(
        todos={"pending_topic_replies": [], "my_open_topics": [{"id": "topic-active"}]},
        topics={"topic-active": topic},
    )

    stats = HostWorker(client, WorkerConfig(once=True, promote_ready_topics=True)).run_once()

    assert stats.experiments_created == 0
    assert client.experiments == []
