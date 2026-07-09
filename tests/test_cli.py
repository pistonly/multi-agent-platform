from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from map_client import project_config
from map_client.testing import MAPTestClientTransport
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

pytestmark = pytest.mark.slow


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli(monkeypatch, client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )


@pytest.fixture
def patched_admin_cli(monkeypatch, client, admin_headers):
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )


def test_cli_admin_create_project(runner, patched_admin_cli):
    result = runner.invoke(
        app,
        ["project", "create", "--key", "cli-project", "--name", "CLI项目", "--path", "/tmp/cli"],
    )
    assert result.exit_code == 0, result.output
    project = yaml.safe_load(result.output)
    assert project["project_key"] == "cli-project"


def test_cli_experiment_flow(runner, patched_cli, project, tmp_path: Path):
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("## CLI plan", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "create",
            "--title",
            "CLI实验",
            "--plan-file",
            str(plan_file),
            "--submit-for-review",
        ],
    )
    assert result.exit_code == 0, result.output
    experiment = yaml.safe_load(result.output)
    assert experiment["phase"] == "review"

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    status = yaml.safe_load(result.output)
    assert status["experiment_counts_by_phase"]["review"] >= 1

    result = runner.invoke(app, ["experiment", "status", "--id", experiment["id"]])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["experiment", "list", "--phase", "review", "--q", "CLI实验", "--page-size", "1"])
    assert result.exit_code == 0, result.output
    experiments = yaml.safe_load(result.output)
    assert len(experiments) == 1
    assert experiments[0]["id"] == experiment["id"]


def test_cli_experiment_status_outputs_acceptance_status(runner, patched_cli, tmp_path: Path):
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(
        "- [acceptance_type: integration] map experiment status shows acceptance",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "experiment",
            "create",
            "--title",
            "CLI acceptance",
            "--plan-file",
            str(plan_file),
        ],
    )
    assert result.exit_code == 0, result.output
    experiment = yaml.safe_load(result.output)

    result = runner.invoke(app, ["experiment", "status", "--id", experiment["id"]])

    assert result.exit_code == 0, result.output
    detail = yaml.safe_load(result.output)
    assert detail["acceptance_status"][0]["acceptance_type"] == "integration"
    assert detail["acceptance_status"][0]["description"] == "map experiment status shows acceptance"


def test_cli_experiment_status_outputs_phase_owner_obligation(
    runner, patched_cli, tmp_path: Path
):
    """f873c287 I1(d): a review-phase experiment whose decision authority
    is held by ``reviewer`` (not host) must surface the ``phase_owner``
    and ``informational_only`` signals in ``map experiment status``
    output so the host knows they are blocked on a reviewer decision.

    The specific blocked_on actionable copy ("reviewer must submit the
    first experiment review") is preserved; the new ``phase_owner:
    reviewer`` line supplements it as the unified "who's holding this"
    signal.
    """
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("## host blocked waiting test", encoding="utf-8")
    res = runner.invoke(
        app,
        [
            "experiment",
            "create",
            "--title",
            "host blocked 实验",
            "--plan-file",
            str(plan_file),
            "--submit-for-review",
        ],
    )
    assert res.exit_code == 0, res.output
    exp = yaml.safe_load(res.output)

    res = runner.invoke(app, ["experiment", "status", "--id", exp["id"]])
    assert res.exit_code == 0, res.output
    # The CLI echoes a mix of free-text lines + final structured YAML.
    # We assert on the free-text portion which is human-facing.
    assert "phase_owner: reviewer" in res.output
    assert "informational_only: True" in res.output
    # The legacy explicit actionable copy for awaiting_non_creator_review
    # is preserved — that's the action, the phase_owner line above is
    # the "who" signal.
    assert "reviewer must submit the first experiment review" in res.output


def test_cli_experiment_status_omits_waiting_copy_for_host_owned(
    runner, patched_cli, tmp_path: Path
):
    """f873c287 I1(d): host-owned phases (e.g. draft, approved) must NOT
    emit the ``waiting on {phase_owner}`` copy — they are actionable by
    the host, not blocked.
    """
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("## host owned draft", encoding="utf-8")
    res = runner.invoke(
        app,
        [
            "experiment",
            "create",
            "--title",
            "host owned draft 实验",
            "--plan-file",
            str(plan_file),
        ],
    )
    assert res.exit_code == 0, res.output
    exp = yaml.safe_load(res.output)

    res = runner.invoke(app, ["experiment", "status", "--id", exp["id"]])
    assert res.exit_code == 0, res.output
    assert "phase_owner: host" in res.output
    assert "informational_only: False" in res.output
    assert "waiting on" not in res.output


def test_cli_experiment_status_no_waiting_copy_when_host_can_approve(
    runner, patched_cli, monkeypatch
):
    """Review-phase experiment where the reviewer raised only reasonable
    items: ``blocked_on="none"`` + ``actions=["approve","withdraw"]`` yet
    ``phase_owner=reviewer`` (feedback a570aa53). The host CAN act, so the
    catch-all must NOT print the misleading
    "waiting on reviewer (blocked_on=none)" copy.

    Uses a stubbed ``get_experiment`` so the assertion does not depend on
    the experiment-create / plan-frontmatter path.
    """
    from map_types.enums import PhaseOwner

    class _FakeResult(SimpleNamespace):
        def model_dump(self, mode="json"):  # noqa: ARG002
            from uuid import UUID

            def _scalar(v):
                if hasattr(v, "value"):
                    return v.value
                if isinstance(v, UUID):
                    return str(v)
                return v

            return {k: _scalar(v) for k, v in self.__dict__.items()}

    def fake_get_experiment(self, experiment_id):  # noqa: ARG001
        return _FakeResult(
            id=experiment_id,
            actions=["approve", "withdraw"],
            blocked_on="none",
            phase_owner=PhaseOwner.reviewer,
            informational_only=False,
            current_plan_version=1,
        )

    monkeypatch.setattr("cli.main.MAPClient.get_experiment", fake_get_experiment)
    result = runner.invoke(
        app,
        ["experiment", "status", "--id", "00000000-0000-0000-0000-000000000001"],
    )
    assert result.exit_code == 0, result.output
    assert "waiting on reviewer" not in result.output
    assert "approve" in result.output  # host sees it can act


def test_cli_complete_submits_result_review(runner, patched_cli, project, reviewer, tmp_path: Path):
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("## CLI plan", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "create",
            "--title",
            "CLI结果审批",
            "--plan-file",
            str(plan_file),
            "--submit-for-review",
        ],
    )
    assert result.exit_code == 0, result.output
    experiment = yaml.safe_load(result.output)
    exp_id = experiment["id"]

    creator_client = cli_main.MAPClient.from_env(transport=cli_main._transport)
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")
    reviewer_client = cli_main.MAPClient("http://test", reviewer_token, transport=cli_main._transport)
    try:
        reviewer_client.create_review(exp_id, cli_main.ReviewCreate(reasonable_items=["OK"]))
        creator_client.approve_experiment(exp_id)
        creator_client.start_experiment(exp_id)
    finally:
        creator_client.close()
        reviewer_client.close()

    log_file = tmp_path / "result.md"
    log_file.write_text(
        "## summary\n提交结果\n\n"
        "## 实施 log\n- [plan](plan.md)\n\n"
        "## 风险\n- 无\n\n"
        "## acceptance\n- [x] (a)\n",
        encoding="utf-8",
    )
    metadata_file = tmp_path / "evidence.yaml"
    metadata_file.write_text(
        yaml.safe_dump({"pytest_summary": "1 passed"}, allow_unicode=True),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "experiment",
            "complete",
            "--id",
            exp_id,
            "--summary",
            "提交结果",
            "--file",
            str(log_file),
            "--metadata",
            str(metadata_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout)["phase"] == "result_review"

    result = runner.invoke(app, ["experiment", "logs", "--id", exp_id])
    assert result.exit_code == 0, result.output
    logs = yaml.safe_load(result.output)
    assert logs[-1]["summary"] == "提交结果"


def test_cli_complete_requires_metadata_evidence(runner, patched_cli, tmp_path: Path):
    log_file = tmp_path / "result.md"
    log_file.write_text("结果内容", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "complete",
            "--id",
            "00000000-0000-0000-0000-000000000001",
            "--summary",
            "提交结果",
            "--file",
            str(log_file),
        ],
    )
    assert result.exit_code == 2
    assert "requires --metadata with deployment/test evidence" in result.output


def test_cli_topic_mark_seen_alias_advances_read_cursor(
    runner, patched_cli, client, auth_headers, reviewer, project
):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "mark-seen alias", "description": "d"},
    ).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer["headers"],
        json={"body": "reviewer update"},
    )

    result = runner.invoke(app, ["topic", "mark-seen", "--id", topic["id"]])

    assert result.exit_code == 0, result.output
    cursor = yaml.safe_load(result.output)
    assert cursor["topic_id"] == topic["id"]
    assert cursor["last_read_comment_seq"] >= 1


def test_cli_pre_complete_validates_metadata_before_api_call(runner, patched_cli, tmp_path: Path):
    metadata_file = tmp_path / "evidence.yaml"
    metadata_file.write_text("note: no evidence\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "pre-complete",
            "--id",
            "00000000-0000-0000-0000-000000000001",
            "--metadata",
            str(metadata_file),
        ],
    )
    assert result.exit_code == 2
    assert "missing completion evidence metadata" in result.output


def test_cli_pre_complete_outputs_jsonable_phase(
    runner, patched_cli, project, reviewer, tmp_path: Path
):
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("## plan", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "create",
            "--title",
            "pre-complete enum",
            "--plan-file",
            str(plan_file),
            "--submit-for-review",
        ],
    )
    assert result.exit_code == 0, result.output
    exp_id = yaml.safe_load(result.output)["id"]

    creator_client = cli_main.MAPClient.from_env(transport=cli_main._transport)
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")
    reviewer_client = cli_main.MAPClient("http://test", reviewer_token, transport=cli_main._transport)
    try:
        reviewer_client.create_review(exp_id, cli_main.ReviewCreate(reasonable_items=["OK"]))
        creator_client.approve_experiment(exp_id)
        creator_client.start_experiment(exp_id)
    finally:
        creator_client.close()
        reviewer_client.close()

    metadata_file = tmp_path / "evidence.yaml"
    metadata_file.write_text("pytest_summary: 1 passed\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "pre-complete",
            "--id",
            exp_id,
            "--metadata",
            str(metadata_file),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["phase"] == "running"
    assert payload["ok"] is True


def test_cli_topic_flow(runner, patched_cli, project, tmp_path: Path):
    result = runner.invoke(app, ["topic", "create", "--title", "CLI话题", "--description", "desc"])
    assert result.exit_code == 0, result.output
    topic = yaml.safe_load(result.output)
    assert topic["status"] == "open"
    topic_id = topic["id"]

    result = runner.invoke(app, ["topic", "comment", "--id", topic_id, "--body", "评论"])
    assert result.exit_code == 0, result.output

    comment_file = tmp_path / "comment.md"
    comment_file.write_text("文件评论", encoding="utf-8")
    result = runner.invoke(app, ["topic", "comment", "--id", topic_id, "--file", str(comment_file)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topic", "show", "--id", topic_id])
    assert result.exit_code == 0, result.output
    detail = yaml.safe_load(result.output)
    assert detail["comment_count"] == 2
    assert detail["discussion_round"] == "round1"

    result = runner.invoke(app, ["topic", "advance-round", "--id", topic_id])
    assert result.exit_code == 0, result.output
    advanced = yaml.safe_load(result.output)
    assert advanced["discussion_round"] == "round2"
    assert advanced["round_summary_count"] == 1

    result = runner.invoke(app, ["topic", "list", "--status", "open", "--q", "CLI话题", "--page-size", "1"])
    assert result.exit_code == 0, result.output
    topics = yaml.safe_load(result.output)
    assert len(topics) == 1
    assert topics[0]["id"] == topic_id

    result = runner.invoke(app, ["topic", "close", "--id", topic_id])
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.output)["status"] == "closed"


def test_cli_topic_resolve_and_action_list(runner, patched_cli, project, reviewer, tmp_path: Path):
    result = runner.invoke(app, ["topic", "create", "--title", "CLI决策话题"])
    assert result.exit_code == 0, result.output
    topic = yaml.safe_load(result.output)

    decision_file = tmp_path / "decision.yaml"
    decision_file.write_text(
        yaml.safe_dump(
            {
                "decision": "采用行动项 MVP",
                "rationale": "先让讨论能沉淀",
                "action_items": [
                    {
                        "title": "补测试",
                        "owner_agent_id": reviewer["id"],
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["topic", "resolve", "--id", topic["id"], "--file", str(decision_file)])
    assert result.exit_code == 0, result.output
    resolved = yaml.safe_load(result.output)
    assert resolved["decision"] == "采用行动项 MVP"
    assert resolved["action_items"][0]["title"] == "补测试"

    result = runner.invoke(app, ["project", "decisions"])
    assert result.exit_code == 0, result.output
    decisions = yaml.safe_load(result.output)
    assert decisions[0]["topic_id"] == topic["id"]

    result = runner.invoke(app, ["action", "list", "--owner-agent-id", reviewer["id"]])
    assert result.exit_code == 0, result.output
    actions = yaml.safe_load(result.output)
    assert actions[0]["title"] == "补测试"


def test_cli_persona_list_missing_map_dir(runner, monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAP_TOKEN", raising=False)
    result = runner.invoke(app, ["persona", "list"])
    assert result.exit_code == 1, result.output
    assert "map bootstrap" in result.output
    assert "config.yaml" in result.output


@pytest.mark.parametrize(
    "argv, kind",
    [
        # pre-complete --metadata: 话题起源场景
        (
            [
                "experiment", "pre-complete",
                "--id", "00000000-0000-0000-0000-000000000001",
                "--metadata", "MISSING",
            ],
            "metadata",
        ),
        # complete --metadata
        (
            [
                "experiment", "complete",
                "--id", "00000000-0000-0000-0000-000000000001",
                "--summary", "x",
                "--file", "MISSING_LOG",
                "--metadata", "MISSING",
            ],
            "metadata",  # metadata 先于 log 解析
        ),
        # complete --file (log)
        (
            [
                "experiment", "complete",
                "--id", "00000000-0000-0000-0000-000000000001",
                "--summary", "x",
                "--file", "MISSING_LOG",
                "--allow-missing-evidence",
            ],
            "log",
        ),
        # experiment log --file
        (
            [
                "experiment", "log",
                "--id", "00000000-0000-0000-0000-000000000001",
                "--summary", "x",
                "--file", "MISSING_LOG",
            ],
            "log",
        ),
        # experiment create --plan-file
        (
            [
                "experiment", "create",
                "--title", "t",
                "--plan-file", "MISSING_PLAN",
            ],
            "plan",
        ),
        # experiment plan revise --plan-file
        (
            [
                "experiment", "plan", "revise",
                "--id", "00000000-0000-0000-0000-000000000001",
                "--plan-file", "MISSING_PLAN",
            ],
            "plan",
        ),
        # experiment review add --review
        (
            [
                "experiment", "review", "add",
                "--id", "00000000-0000-0000-0000-000000000001",
                "--review", "MISSING_REVIEW",
            ],
            "review",
        ),
        # topic comment --file
        (
            [
                "topic", "comment",
                "--id", "00000000-0000-0000-0000-000000000001",
                "--file", "MISSING_COMMENT",
            ],
            "comment",
        ),
    ],
)
def test_cli_missing_file_args_emit_clean_error_not_traceback(
    runner, patched_cli, tmp_path: Path, argv, kind
):
    """File-reading commands must convert missing-file OS errors into a clean
    CLI error (exit 2) and never surface a Python traceback.

    Regression for topic: pre-complete metadata 文件不存在时不应输出 Python traceback.
    """
    # Replace MISSING placeholders with non-existent paths inside tmp_path.
    missing_path = tmp_path / f"missing_{kind}.yaml"
    assert not missing_path.exists()
    real_argv = [missing_path.as_posix() if a.startswith("MISSING") else a for a in argv]

    result = runner.invoke(app, real_argv)

    assert result.exit_code == 2, result.output
    # Friendly message names the kind of file and the path.
    assert f"{kind} file not found" in result.output
    assert str(missing_path) in result.output
    # No Python traceback should leak to stdout/stderr.
    assert "Traceback (most recent call last)" not in result.output
    assert "FileNotFoundError" not in result.output


def test_cli_pre_complete_missing_metadata_directory_error(
    runner, patched_cli, tmp_path: Path
):
    """Passing a directory as --metadata must also produce a clean exit 2."""
    directory = tmp_path / "is-a-dir"
    directory.mkdir()
    result = runner.invoke(
        app,
        [
            "experiment", "pre-complete",
            "--id", "00000000-0000-0000-0000-000000000001",
            "--metadata", str(directory),
        ],
    )
    assert result.exit_code == 2, result.output
    assert "metadata path is a directory" in result.output
    assert "Traceback" not in result.output


def test_cli_experiment_status_persona_compare_diffs_per_actor_view(
    runner, monkeypatch, tmp_path: Path
):
    """0db51e10 I1(5a): ``map experiment status --persona-compare`` fetches
    the experiment once per persona using that persona's own bearer token
    and renders a diff table over the four per-actor fields (``actions``,
    ``blocked_on``, ``phase_owner``, ``informational_only``). When the
    views disagree on any field, that field is listed under
    ``diff fields (vary across personas)``.
    """
    fake_config = SimpleNamespace(
        api_url="http://test",
        tokens={"host": "h-tok", "reviewer": "r-tok", "participant": "p-tok"},
    )
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(cli_main, "load_project_map_config", lambda **kw: fake_config)

    views_by_token = {
        "h-tok": SimpleNamespace(
            actions=["complete"],
            blocked_on=None,
            phase_owner="host",
            informational_only=False,
        ),
        "r-tok": SimpleNamespace(
            actions=[],
            blocked_on="awaiting_result_approval",
            phase_owner="reviewer",
            informational_only=True,
        ),
        "p-tok": SimpleNamespace(
            actions=[],
            blocked_on="awaiting_result_approval",
            phase_owner="reviewer",
            informational_only=True,
        ),
    }

    captured_tokens: list[str] = []

    def fake_get_experiment(self, experiment_id):  # noqa: ARG001
        captured_tokens.append(self.token)
        return views_by_token[self.token]

    monkeypatch.setattr("cli.main.MAPClient.get_experiment", fake_get_experiment)

    result = runner.invoke(
        app,
        [
            "experiment",
            "status",
            "--id",
            "00000000-0000-0000-0000-000000000001",
            "--persona-compare",
        ],
    )

    assert result.exit_code == 0, result.output
    # Three personas, each fetched exactly once with its own token.
    assert sorted(captured_tokens) == ["h-tok", "p-tok", "r-tok"]
    # Diff table contains the four diff fields and the per-persona values.
    assert "persona_compare" in result.output
    assert "actions" in result.output
    assert "blocked_on" in result.output
    assert "phase_owner" in result.output
    assert "informational_only" in result.output
    assert "complete" in result.output  # host's action
    assert "awaiting_result_approval" in result.output  # reviewer / participant blocked_on
    # actions / blocked_on / phase_owner / informational_only all differ
    # between host and reviewer (host can complete, reviewer cannot).
    assert "diff fields (vary across personas):" in result.output


def test_cli_experiment_status_persona_compare_raw_dumps_full_views(
    runner, monkeypatch, tmp_path: Path
):
    """0db51e10 I1(5a): ``--raw`` switches the persona-compare output to
    the full per-persona YAML snapshot, useful for snapshot tests and
    e2e diff review.
    """
    fake_config = SimpleNamespace(
        api_url="http://test",
        tokens={"host": "h-tok", "reviewer": "r-tok"},
    )
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(cli_main, "load_project_map_config", lambda **kw: fake_config)

    views_by_token = {
        "h-tok": SimpleNamespace(
            actions=["complete"],
            blocked_on=None,
            phase_owner="host",
            informational_only=False,
            extra_field="host-only",
        ),
        "r-tok": SimpleNamespace(
            actions=[],
            blocked_on="awaiting_result_approval",
            phase_owner="reviewer",
            informational_only=True,
            extra_field="reviewer-only",
        ),
    }

    def fake_get_experiment(self, experiment_id):  # noqa: ARG001
        return views_by_token[self.token]

    monkeypatch.setattr("cli.main.MAPClient.get_experiment", fake_get_experiment)

    result = runner.invoke(
        app,
        [
            "experiment",
            "status",
            "--id",
            "00000000-0000-0000-0000-000000000001",
            "--persona-compare",
            "--raw",
            "--for-personas",
            "host,reviewer",
        ],
    )

    assert result.exit_code == 0, result.output
    # YAML payload contains both personas AND the extra_field that the
    # default diff table does not surface.
    assert "host:" in result.output
    assert "reviewer:" in result.output
    assert "host-only" in result.output
    assert "reviewer-only" in result.output


def test_cli_experiment_status_persona_compare_for_personas_limits_subset(
    runner, monkeypatch, tmp_path: Path
):
    """0db51e10 I1(5a): ``--for-personas host,reviewer`` overrides the
    default "all known personas" selection and limits the diff to the
    listed subset. Tokens for unselected personas are never used.
    """
    fake_config = SimpleNamespace(
        api_url="http://test",
        tokens={"host": "h-tok", "reviewer": "r-tok", "participant": "p-tok"},
    )
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(cli_main, "load_project_map_config", lambda **kw: fake_config)

    views_by_token = {
        "h-tok": SimpleNamespace(actions=["complete"], blocked_on=None, phase_owner="host", informational_only=False),
        "r-tok": SimpleNamespace(actions=[], blocked_on="awaiting_result_approval", phase_owner="reviewer", informational_only=True),
        "p-tok": SimpleNamespace(actions=[], blocked_on="awaiting_result_approval", phase_owner="reviewer", informational_only=True),
    }

    captured_tokens: list[str] = []

    def fake_get_experiment(self, experiment_id):  # noqa: ARG001
        captured_tokens.append(self.token)
        return views_by_token[self.token]

    monkeypatch.setattr("cli.main.MAPClient.get_experiment", fake_get_experiment)

    result = runner.invoke(
        app,
        [
            "experiment",
            "status",
            "--id",
            "00000000-0000-0000-0000-000000000001",
            "--persona-compare",
            "--for-personas",
            "host,reviewer",
        ],
    )

    assert result.exit_code == 0, result.output
    # Only host + reviewer fetched; participant token untouched.
    assert sorted(captured_tokens) == ["h-tok", "r-tok"]
    assert "p-tok" not in captured_tokens


def test_cli_experiment_status_persona_compare_writes_audit(
    runner, monkeypatch, tmp_path: Path
):
    """0db51e10 I2(5e): after rendering the diff table, the CLI calls
    ``MAPClient.record_cross_persona_call`` on the host-token client with
    the per-field persona view diff, ``result_partition_count``, and
    ``diff_size``.
    """
    fake_config = SimpleNamespace(
        api_url="http://test",
        tokens={"host": "h-tok", "reviewer": "r-tok"},
    )
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(cli_main, "load_project_map_config", lambda **kw: fake_config)

    # The host client passed into ``_persona_compare_view`` is the default
    # ``_run`` client. Replace ``_client_ctx`` to yield a host-token client.
    from contextlib import contextmanager

    host_client = cli_main.MAPClient("http://test", token="h-tok")

    @contextmanager
    def fake_client_ctx(*args, **kwargs):
        yield host_client

    monkeypatch.setattr(cli_main, "_client_ctx", fake_client_ctx)

    views_by_token = {
        "h-tok": SimpleNamespace(
            actions=["complete"],
            blocked_on=None,
            phase_owner="host",
            informational_only=False,
        ),
        "r-tok": SimpleNamespace(
            actions=[],
            blocked_on="awaiting_result_approval",
            phase_owner="reviewer",
            informational_only=True,
        ),
    }

    def fake_get_experiment(self, experiment_id):  # noqa: ARG001
        return views_by_token[self.token]

    monkeypatch.setattr("cli.main.MAPClient.get_experiment", fake_get_experiment)

    audit_calls: list[dict] = []

    def fake_record_cross_persona_call(
        self, experiment_id, *, visibility_diff=None, result_partition_count=0, diff_size=0
    ):
        audit_calls.append(
            {
                "experiment_id": experiment_id,
                "visibility_diff": visibility_diff,
                "result_partition_count": result_partition_count,
                "diff_size": diff_size,
                "token": self.token,
            }
        )
        return SimpleNamespace(id="audit-row-uuid")

    monkeypatch.setattr(
        "cli.main.MAPClient.record_cross_persona_call",
        fake_record_cross_persona_call,
    )

    result = runner.invoke(
        app,
        [
            "experiment",
            "status",
            "--id",
            "00000000-0000-0000-0000-000000000001",
            "--persona-compare",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(audit_calls) == 1, audit_calls
    call = audit_calls[0]
    # Audit always uses the host-token client (the one supplied to ``_run``).
    assert call["token"] == "h-tok"
    # 2 personas fetched, 4 diff fields → 4 differ (host and reviewer disagree
    # on all four: actions / blocked_on / phase_owner / informational_only).
    assert call["result_partition_count"] == 2
    assert call["diff_size"] == 4
    # visibility_diff carries the per-field per-persona view dict for all
    # four audit fields, even when they don't differ.
    # 0db51e10 I4(5a partition): also carries the four-way
    # acceptance_status partition label produced by
    # ``_classify_persona_compare_partition``.
    assert set(call["visibility_diff"].keys()) == {
        "actions",
        "blocked_on",
        "phase_owner",
        "informational_only",
        "acceptance_status_partition",
    }
    assert call["visibility_diff"]["acceptance_status_partition"] in {
        "all_agree",
        "partial_diff",
        "full_diff",
        "cross_phase_fold",
    }
    assert call["visibility_diff"]["actions"] == {"host": ["complete"], "reviewer": []}
    assert call["visibility_diff"]["phase_owner"] == {"host": "host", "reviewer": "reviewer"}


def test_cli_experiment_status_persona_compare_audit_failure_does_not_break_output(
    runner, monkeypatch, tmp_path: Path
):
    """0db51e10 I2(5e): audit write failures are best-effort — a 500 from
    ``record_cross_persona_call`` must NOT mask the user-facing diff
    table. The CLI surfaces the failure on stderr and exits 0.
    """
    fake_config = SimpleNamespace(
        api_url="http://test",
        tokens={"host": "h-tok", "reviewer": "r-tok"},
    )
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(cli_main, "load_project_map_config", lambda **kw: fake_config)

    from contextlib import contextmanager

    host_client = cli_main.MAPClient("http://test", token="h-tok")

    @contextmanager
    def fake_client_ctx(*args, **kwargs):
        yield host_client

    monkeypatch.setattr(cli_main, "_client_ctx", fake_client_ctx)

    views_by_token = {
        "h-tok": SimpleNamespace(
            actions=["complete"], blocked_on=None, phase_owner="host", informational_only=False
        ),
        "r-tok": SimpleNamespace(
            actions=[], blocked_on="x", phase_owner="reviewer", informational_only=True
        ),
    }

    def fake_get_experiment(self, experiment_id):  # noqa: ARG001
        return views_by_token[self.token]

    monkeypatch.setattr("cli.main.MAPClient.get_experiment", fake_get_experiment)

    def fake_record_cross_persona_call_fail(
        self, experiment_id, *, visibility_diff=None, result_partition_count=0, diff_size=0
    ):
        raise RuntimeError("audit backend 500")

    monkeypatch.setattr(
        "cli.main.MAPClient.record_cross_persona_call",
        fake_record_cross_persona_call_fail,
    )

    result = runner.invoke(
        app,
        [
            "experiment",
            "status",
            "--id",
            "00000000-0000-0000-0000-000000000001",
            "--persona-compare",
        ],
    )

    # Diff table still renders, even though the audit write raised.
    assert result.exit_code == 0, result.output
    assert "diff fields (vary across personas):" in result.output
    # Failure surfaces on stderr, not stdout, so the YAML / table layout
    # is not corrupted.
    assert "audit write failed" in (result.stderr or "") or "audit write failed" in result.output
