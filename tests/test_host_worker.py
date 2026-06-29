import json
import sys
from pathlib import Path
from typing import Any

from cli.host_worker import HostWorker, WorkerConfig


class FakeMapClient:
    def __init__(self, *, todos: dict[str, Any], topics: dict[str, dict[str, Any]]) -> None:
        self._todos = todos
        self._topics = topics
        self.comments: list[dict[str, Any]] = []
        self.experiments: list[dict[str, Any]] = []
        self.advances: list[str] = []

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

    def topic_advance_round(self, topic_id: str) -> dict[str, Any]:
        self.advances.append(topic_id)
        return {"id": topic_id}

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


def _runner_script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "runner.py"
    path.write_text(body, encoding="utf-8")
    return f"{sys.executable} {path}"


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


def test_worker_agent_runner_writes_reply_and_state(tmp_path):
    runner = _runner_script(
        tmp_path,
        """
import json
import sys

req = json.loads(sys.stdin.readline())
assert req["action"] == "reply_pending"
print(json.dumps({"body": "runner reply", "parent_id": req["context"]["pending_item"]["comment_id"]}))
""",
    )
    state_file = tmp_path / "host-bridge-state.json"
    client = FakeMapClient(
        todos={
            "pending_topic_replies": [
                {
                    "topic_id": "topic-1",
                    "comment_id": "comment-1",
                    "thread_root_id": "comment-1",
                    "excerpt": "需要 runner",
                }
            ],
            "my_open_topics": [],
        },
        topics={
            "topic-1": {
                "id": "topic-1",
                "title": "bridge",
                "discussion_round": "round1",
                "round_summary_count": 0,
            }
        },
    )

    stats = HostWorker(
        client,
        WorkerConfig(once=True, agent_runner=runner, state_file=state_file),
    ).run_once()

    assert stats.runner_invocations == 1
    assert stats.replies_created == 1
    assert client.comments == [{"topic_id": "topic-1", "body": "runner reply", "parent_id": "comment-1"}]
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert state["topics"]["topic-1"]["last_handled_comment_id"] == "comment-1"
    assert state["topics"]["topic-1"]["last_action_at"]


def test_worker_agent_runner_error_does_not_write_or_update_state(tmp_path):
    runner = _runner_script(
        tmp_path,
        """
import sys

print("runner failed", file=sys.stderr)
raise SystemExit(1)
""",
    )
    state_file = tmp_path / "host-bridge-state.json"
    client = FakeMapClient(
        todos={
            "pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}],
            "my_open_topics": [],
        },
        topics={"topic-1": {"id": "topic-1", "discussion_round": "round1", "round_summary_count": 0}},
    )

    stats = HostWorker(
        client,
        WorkerConfig(once=True, agent_runner=runner, state_file=state_file),
    ).run_once()

    assert stats.runner_errors == 1
    assert client.comments == []
    assert not state_file.exists()


def test_worker_agent_runner_skip_updates_state_to_avoid_retries(tmp_path):
    skip_runner = _runner_script(
        tmp_path,
        """
raise SystemExit(2)
""",
    )
    state_file = tmp_path / "host-bridge-state.json"
    todos = {
        "pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}],
        "my_open_topics": [],
    }
    topics = {"topic-1": {"id": "topic-1", "discussion_round": "round1", "round_summary_count": 0}}
    client = FakeMapClient(todos=todos, topics=topics)

    first = HostWorker(
        client,
        WorkerConfig(once=True, agent_runner=skip_runner, state_file=state_file),
    ).run_once()

    assert first.runner_skips == 1
    assert client.comments == []
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["topics"]["topic-1"]["last_handled_comment_id"] == "comment-1"

    failing_runner = _runner_script(
        tmp_path,
        """
raise SystemExit(1)
""",
    )
    second = HostWorker(
        client,
        WorkerConfig(once=True, agent_runner=failing_runner, state_file=state_file),
    ).run_once()

    assert second.runner_invocations == 0
    assert second.runner_errors == 0
    assert second.runner_skips == 1
    assert client.comments == []


def test_worker_agent_runner_dry_run_invokes_runner_without_writing_state(tmp_path):
    runner = _runner_script(
        tmp_path,
        """
import json
import sys

req = json.loads(sys.stdin.readline())
assert req["dry_run"] is True
print(json.dumps({"body": "dry reply"}))
""",
    )
    state_file = tmp_path / "host-bridge-state.json"
    client = FakeMapClient(
        todos={
            "pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}],
            "my_open_topics": [],
        },
        topics={"topic-1": {"id": "topic-1", "discussion_round": "round1", "round_summary_count": 0}},
    )

    stats = HostWorker(
        client,
        WorkerConfig(once=True, dry_run=True, agent_runner=runner, state_file=state_file),
    ).run_once()

    assert stats.runner_invocations == 1
    assert stats.dry_run_actions == 1
    assert client.comments == []
    assert not state_file.exists()


def test_worker_agent_runner_promote_uses_runner_plan(tmp_path):
    runner = _runner_script(
        tmp_path,
        """
import json

print(json.dumps({"body": "# Runner plan", "create_experiment": True}))
""",
    )
    state_file = tmp_path / "host-bridge-state.json"
    topic = {
        "id": "topic-ready",
        "title": "bridge promote",
        "discussion_round": "ready",
        "round_summary_count": 2,
        "experiments": [],
        "comments": [
            {"id": "c1", "author_agent_id": "participant-agent", "body": "同意", "children": []},
            {"id": "c2", "author_agent_id": "host-agent", "body": "Round 1 Summary", "children": []},
            {"id": "c3", "author_agent_id": "host-agent", "body": "Round 2 Summary", "children": []},
        ],
    }
    client = FakeMapClient(
        todos={"pending_topic_replies": [], "my_open_topics": [{"id": "topic-ready"}]},
        topics={"topic-ready": topic},
    )

    stats = HostWorker(
        client,
        WorkerConfig(
            once=True,
            agent_runner=runner,
            promote_ready_topics=True,
            state_file=state_file,
        ),
    ).run_once()

    assert stats.runner_invocations == 1
    assert stats.experiments_created == 1
    assert client.experiments[0]["plan"] == "# Runner plan"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["topics"]["topic-ready"]["last_round_summary_count"] == 2


def test_worker_lifecycle_posts_round_summary_and_advances(tmp_path):
    runner = _runner_script(
        tmp_path,
        """
import json
import sys

req = json.loads(sys.stdin.readline())
assert req["action"] == "round_summary"
assert req["context"]["round_n"] == 1
print(json.dumps({
    "body": "## Round 1 Summary\\n\\n### 已共识\\n- 同意纳入 v0.8",
    "parent_id": None,
    "advance_round": True,
}))
""",
    )
    state_file = tmp_path / "host-bridge-state.json"
    topic = {
        "id": "topic-r1",
        "title": "干净测试",
        "description": "bridge 应发 summary",
        "discussion_round": "round1",
        "round_summary_count": 0,
        "experiments": [],
        "comments": [
            {
                "id": "c1",
                "author_agent_id": "participant-agent",
                "body": "支持纳入默认部署",
                "children": [
                    {
                        "id": "c2",
                        "author_agent_id": "host-agent",
                        "parent_comment_id": "c1",
                        "body": "收到，请补充验收项",
                        "children": [],
                    },
                    {
                        "id": "c3",
                        "author_agent_id": "participant-agent",
                        "parent_comment_id": "c1",
                        "body": "验收项：三桥日志可 tail",
                        "children": [],
                    },
                ],
            }
        ],
    }
    client = FakeMapClient(
        todos={"pending_topic_replies": [], "my_open_topics": [{"id": "topic-r1"}]},
        topics={"topic-r1": topic},
    )

    stats = HostWorker(
        client,
        WorkerConfig(once=True, agent_runner=runner, state_file=state_file, manage_topic_lifecycle=True),
    ).run_once()

    assert stats.runner_invocations == 1
    assert stats.summaries_created == 1
    assert stats.round_advances == 1
    assert client.advances == ["topic-r1"]
    assert client.comments[0]["parent_id"] is None
    assert "## Round 1 Summary" in client.comments[0]["body"]


def test_worker_lifecycle_skips_summary_when_pending_replies_exist(tmp_path):
    reply_runner = _runner_script(
        tmp_path,
        """
import json
import sys

req = json.loads(sys.stdin.readline())
assert req["action"] == "reply_pending"
print(json.dumps({"body": "host reply", "parent_id": req["context"]["pending_item"]["comment_id"]}))
""",
    )
    topic = {
        "id": "topic-pending",
        "title": "有待回复",
        "discussion_round": "round1",
        "round_summary_count": 0,
        "experiments": [],
        "comments": [
            {"id": "c1", "author_agent_id": "participant-agent", "body": "意见", "children": []},
        ],
    }
    client = FakeMapClient(
        todos={
            "pending_topic_replies": [{"topic_id": "topic-pending", "comment_id": "c1"}],
            "my_open_topics": [{"id": "topic-pending"}],
        },
        topics={"topic-pending": topic},
    )

    stats = HostWorker(
        client,
        WorkerConfig(
            once=True,
            agent_runner=reply_runner,
            manage_topic_lifecycle=True,
            state_file=tmp_path / "host-bridge-state.json",
        ),
    ).run_once()

    assert stats.replies_created == 1
    assert stats.summaries_created == 0
    assert stats.round_advances == 0
