"""8a8822b5 (b)(c): error envelope Pydantic schema + ``--file`` parity.

Pins plan v2 (b) and (c) acceptance from the CLI side:

* (b) error envelope schema = ``{error_code, message, hint?, docs_url?,
  retryable?, recovery_command?}`` validated through the
  ``CLIErrorEnvelope`` Pydantic BaseModel on every JSON error path.
* (c) every command that accepts ``--body`` also accepts ``--file`` with
  the same mutually-exclusive validation as ``map topic comment``; text
  commands may also use non-interactive stdin.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from map_client.exceptions import MAPNotFoundError
from pydantic import ValidationError
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from cli.runner import CLIErrorEnvelope, _emit_json_error_envelope

# ---- fixtures --------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli_no_server(monkeypatch, client, auth_headers):
    """MAP client transport pointed at the in-process TestClient; no live API."""

    from map_client.testing import MAPTestClientTransport

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


# ---- (b) Pydantic envelope -------------------------------------------------


def test_cli_error_envelope_pydantic_required_fields():
    """message is the only required field per the schema."""
    envelope = CLIErrorEnvelope(message="boom")
    assert envelope.message == "boom"
    assert envelope.error_code is None
    assert envelope.hint is None
    assert envelope.docs_url is None
    assert envelope.retryable is None
    assert envelope.recovery_command is None


def test_cli_error_envelope_pydantic_all_fields():
    envelope = CLIErrorEnvelope(
        error_code="REVIEW_REJECT_RESULT_MISUSE",
        message="reject-result misuse",
        hint="use complete instead",
        docs_url="https://example.invalid/docs/MAP-ERROR-CODES.md#review_reject_result_misuse",
        retryable=False,
        recovery_command="map experiment complete",
    )
    dumped = envelope.model_dump_json()
    parsed = json.loads(dumped)
    assert parsed["error_code"] == "REVIEW_REJECT_RESULT_MISUSE"
    assert parsed["docs_url"].startswith("https://")


def test_cli_error_envelope_pydantic_forbids_extra_fields():
    """Adding an unexpected field must fail validation."""
    with pytest.raises(ValidationError):
        CLIErrorEnvelope(message="x", unknown_field="oops")  # type: ignore[call-arg]


def test_cli_error_envelope_pydantic_rejects_missing_message():
    with pytest.raises(ValidationError):
        CLIErrorEnvelope()  # type: ignore[call-arg]


def test_cli_emit_json_error_envelope_writes_pydantic_json(runner, monkeypatch, capsys):
    """``_emit_json_error_envelope`` emits a Pydantic-validated JSON line.

    b56a232 起 stderr 行为外层信封 ``{"ok": false, "error": {...}}``
    （docs/cli-json-output.md 契约），内层 error 才是 ``CLIErrorEnvelope``
    字段集。此处解包后 re-validate，保证打印的形状可被规范模型回读。
    """
    _emit_json_error_envelope(
        error_code="STATE_MACHINE_INVALID_PHASE",
        message="transition rejected",
        hint="submit for review first",
        retryable=False,
        recovery_command="map experiment submit-review",
        docs_url=None,
    )
    captured = capsys.readouterr()
    line = next(
        (ln for ln in captured.err.splitlines() if ln.startswith("{")),
        None,
    )
    assert line is not None, captured.err
    outer = json.loads(line)
    assert outer["ok"] is False
    parsed = CLIErrorEnvelope.model_validate(outer["error"])
    assert parsed.error_code == "STATE_MACHINE_INVALID_PHASE"
    assert parsed.hint == "submit for review first"
    assert parsed.recovery_command == "map experiment submit-review"
    assert parsed.docs_url is None


def test_cli_json_envelope_includes_docs_url_field(runner, patched_cli_no_server):
    """End-to-end: --format json emits an envelope whose key set pins docs_url.

    Triggers a 404 against ``map experiment start --id <random>`` and asserts
    the printed envelope declares ``docs_url`` (may be null). This is the
    regression test for plan (b).
    """
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "experiment",
            "start",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    envelope_line = next(
        (ln for ln in result.stderr.splitlines() if ln.startswith("{")),
        None,
    )
    assert envelope_line, result.stderr
    outer = json.loads(envelope_line)
    assert outer["ok"] is False
    envelope = outer["error"]
    assert "docs_url" in envelope
    # Round-trip parse through the Pydantic model.
    parsed = CLIErrorEnvelope.model_validate(envelope)
    # docs_url is optional — accept None (server doesn't emit one yet) but
    # the field must always be declared.
    assert hasattr(parsed, "docs_url")
    # All v1 stable fields are present in the inner error object.
    assert set(envelope.keys()) == {
        "error_code",
        "message",
        "hint",
        "docs_url",
        "retryable",
        "recovery_command",
    }


def test_cli_maphttp_error_envelope_uses_pydantic(runner, monkeypatch, capsys):
    """MAPHTTPError path (not ValueError) still emits a Pydantic envelope."""

    # Build a one-shot MAPClient whose transport raises MAPNotFoundError.
    class _RaisingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def handle_request(self, request):  # pragma: no cover - exercise only
            self.calls += 1
            raise MAPNotFoundError(404, "not here", error_code="NOT_FOUND", hint="check id")

    transport = _RaisingTransport()
    monkeypatch.setattr(cli_main, "_transport", transport)
    monkeypatch.setenv("MAP_TOKEN", "fake")
    monkeypatch.setenv("MAP_API_URL", "http://test")

    result = runner.invoke(
        app,
        ["--format", "json", "experiment", "show", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    envelope_line = next(
        (ln for ln in result.stderr.splitlines() if ln.startswith("{")),
        None,
    )
    assert envelope_line, result.stderr
    outer = json.loads(envelope_line)
    assert outer["ok"] is False
    parsed = CLIErrorEnvelope.model_validate(outer["error"])
    assert parsed.error_code == "NOT_FOUND"
    assert parsed.message == "not here"
    assert parsed.hint == "check id"


# ---- (c) --file parity -----------------------------------------------------


def test_experiment_comment_accepts_file(patched_cli_no_server, runner, tmp_path):
    """``map experiment comment --file`` reads body from a file (no shell quotes)."""
    body_file = tmp_path / "comment.md"
    body_file.write_text("来自文件的评论\n\n含反引号 `code` 和 $var 不会被 shell 吞掉\n", encoding="utf-8")

    # The patched client does not actually create a real experiment here;
    # we only need the CLI to (a) parse --file and (b) attempt the SDK call.
    # The endpoint will 404 / 400 — but the body-reading must succeed.
    result = runner.invoke(
        app,
        [
            "experiment",
            "comment",
            "--id",
            str(uuid.uuid4()),
            "--anchor-type",
            "experiment",
            "--anchor-id",
            str(uuid.uuid4()),
            "--file",
            str(body_file),
        ],
    )
    # The exit code is non-zero because the anchor is fake; but the
    # failure must NOT be "either --body or --file is required" or
    # "use only one of --body or --file".
    combined = (result.stdout or "") + (result.stderr or "")
    assert "either --body or --file is required" not in combined
    assert "use only one of --body or --file" not in combined
    # And the body-file content must have been read (no FileNotFoundError).
    assert "No such file" not in combined


def test_experiment_comment_accepts_piped_stdin(monkeypatch, runner):
    import cli.commands.experiment as experiment_cli

    fake_client = MagicMock()
    monkeypatch.setattr(
        experiment_cli.runner,
        "_run",
        lambda action, **_kwargs: action(fake_client),
    )
    result = runner.invoke(
        app,
        [
            "experiment",
            "comment",
            "--id",
            str(uuid.uuid4()),
            "--anchor-type",
            "comment",
            "--anchor-id",
            str(uuid.uuid4()),
        ],
        input="来自 stdin 的评论\n含 `反引号` 和 $变量。\n",
    )
    assert result.exit_code == 0, result.output
    payload = fake_client.create_comment.call_args.args[1]
    assert payload.body == "来自 stdin 的评论\n含 `反引号` 和 $变量。\n"


def test_experiment_comment_body_and_file_mutually_exclusive(patched_cli_no_server, runner, tmp_path):
    """Supplying both --body and --file exits 2 with a friendly error."""
    body_file = tmp_path / "comment.md"
    body_file.write_text("from file", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "comment",
            "--id",
            str(uuid.uuid4()),
            "--anchor-type",
            "experiment",
            "--anchor-id",
            str(uuid.uuid4()),
            "--body",
            "from arg",
            "--file",
            str(body_file),
        ],
    )
    assert result.exit_code == 2
    assert "use only one of --body or --file" in (result.stderr or "")


def test_experiment_comment_requires_body_or_file(patched_cli_no_server, runner):
    """Without a TTY, empty stdin still produces a useful content-source error."""
    result = runner.invoke(
        app,
        [
            "experiment",
            "comment",
            "--id",
            str(uuid.uuid4()),
            "--anchor-type",
            "experiment",
            "--anchor-id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code == 2
    assert "provide --body, --file, or pipe comment text on stdin" in (result.stderr or "")


def test_feedback_submit_accepts_file(patched_cli_no_server, runner, tmp_path):
    body_file = tmp_path / "feedback.md"
    body_file.write_text("`backticks` and $vars survive heredoc-free", encoding="utf-8")
    result = runner.invoke(app, ["feedback", "submit", "--file", str(body_file)])
    # The exit code may be non-zero (no project context / admin scope), but
    # the body-reading must have succeeded without complaining.
    combined = (result.stdout or "") + (result.stderr or "")
    assert "either --body or --file is required" not in combined
    assert "use only one of --body or --file" not in combined
    assert "No such file" not in combined


# M62 退役后 ``map feedback submit`` 无条件 exit 2（参数校验契约随之消失），
# stub 行为由 tests/test_feedback_stub.py 全量覆盖，旧参数校验断言不再适用。


# ---- cross-command parity --------------------------------------------------


@pytest.mark.parametrize(
    "argv_tail",
    [
        # Each entry is the suffix of a command that accepts --body text.
        # The contract: when --file is provided (and --body absent) the
        # CLI must NOT emit "either --body or --file is required".
        # The endpoints will 4xx because the UUIDs are random, but the
        # body-reading step happens before any HTTP call.
        ["experiment", "comment", "--id", "{eid}", "--anchor-type", "experiment",
         "--anchor-id", "{aid}", "--file", "{path}"],
        ["feedback", "submit", "--file", "{path}"],
    ],
)
def test_all_body_commands_accept_file(patched_cli_no_server, runner, tmp_path, argv_tail):
    body_file = tmp_path / "body.md"
    body_file.write_text("`backticks` & $vars", encoding="utf-8")
    argv = [tok.format(eid=uuid.uuid4(), aid=uuid.uuid4(), path=body_file) for tok in argv_tail]
    result = runner.invoke(app, argv)
    combined = (result.stdout or "") + (result.stderr or "")
    assert "either --body or --file is required" not in combined
    assert "use only one of --body or --file" not in combined
    assert "No such file" not in combined
