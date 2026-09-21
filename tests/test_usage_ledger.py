"""CLI 出口记账测量面（实验 4e4206de I7 / A7）。

钉住验收：
- 每次调用落一行 JSONL ``{ts, persona, kind, cmd, output_bytes, exit_code}``；
  ``kind`` 取 ``MAP_WAKE_KINDS`` env，非唤醒调用为 null。
- 落点 ``<map_dir>/usage/cli-calls.jsonl``；记账失败绝不影响 CLI（best-effort）。
- ``map usage summary --since`` 是单一机器可判路径：按 persona 聚合窗口内
  ``{persona, work_calls, total_output_bytes}``。
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

import cli.commands.usage as usage_cmd
from cli.main import app
from cli.usage_ledger import (
    KINDS_ENV,
    CliCallRecord,
    CountingStdout,
    append_record,
    exit_code_from_exc,
    extract_cmd,
    ledger_path,
    parse_since,
    read_records,
    record_cli_call,
    summarize,
    wake_kind_from_env,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- wake kind env ----------------------------------------------------------


def test_wake_kind_from_env_unset_is_none(monkeypatch):
    monkeypatch.delenv(KINDS_ENV, raising=False)
    assert wake_kind_from_env() is None


def test_wake_kind_from_env_blank_is_none():
    assert wake_kind_from_env({KINDS_ENV: "   "}) is None


def test_wake_kind_from_env_passthrough():
    assert wake_kind_from_env({KINDS_ENV: "my_open_experiments,mentions"}) == "my_open_experiments,mentions"


# --- parse_since ------------------------------------------------------------


def test_parse_since_accepts_z_and_naive():
    z = parse_since("2026-09-21T10:00:00Z")
    naive = parse_since("2026-09-21T10:00:00")
    assert z.tzinfo is not None and naive.tzinfo is not None
    assert z == naive


def test_parse_since_rejects_garbage():
    with pytest.raises(ValueError):
        parse_since("not-a-timestamp")


# --- record round-trip ------------------------------------------------------


def test_append_and_read_roundtrip(tmp_path: Path):
    rec = CliCallRecord(
        ts="2026-09-21T10:00:00+00:00",
        persona="host",
        kind="my_open_experiments",
        cmd="experiment complete",
        output_bytes=512,
        exit_code=0,
    )
    append_record(tmp_path, rec)
    rows = list(read_records(tmp_path))
    assert rows == [rec.to_dict()]
    assert ledger_path(tmp_path).exists()


def test_read_records_tolerates_missing_and_corrupt(tmp_path: Path):
    assert list(read_records(tmp_path)) == []  # 缺文件
    path = ledger_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"ts": "x"}\n\n{not json}\n{"output_bytes": 3}\n', encoding="utf-8")
    rows = list(read_records(tmp_path))
    assert rows == [{"ts": "x"}, {"output_bytes": 3}]  # 空行/损坏行跳过


# --- summarize --------------------------------------------------------------


def _rec(persona, out_bytes, ts, kind=None):
    return {
        "ts": ts,
        "persona": persona,
        "kind": kind,
        "cmd": "x",
        "output_bytes": out_bytes,
        "exit_code": 0,
    }


def test_summarize_groups_by_persona_and_sums_bytes():
    records = [
        _rec("host", 100, "2026-09-21T10:00:00+00:00"),
        _rec("host", 250, "2026-09-21T10:05:00+00:00"),
        _rec("participant", 40, "2026-09-21T10:01:00+00:00"),
    ]
    rows = {r.persona: r for r in summarize(records, since=None)}
    assert rows["host"].work_calls == 2
    assert rows["host"].total_output_bytes == 350
    assert rows["participant"].work_calls == 1
    assert rows["participant"].total_output_bytes == 40


def test_summarize_since_filters_older_and_unparseable():
    records = [
        _rec("host", 100, "2026-09-21T09:00:00+00:00"),  # before window
        _rec("host", 200, "2026-09-21T11:00:00+00:00"),  # in window
        _rec("host", 999, "garbage-ts"),  # unparseable → excluded when since given
    ]
    since = parse_since("2026-09-21T10:00:00Z")
    rows = summarize(records, since=since)
    assert len(rows) == 1
    assert rows[0].work_calls == 1
    assert rows[0].total_output_bytes == 200


def test_summarize_missing_persona_buckets_as_none():
    records = [_rec(None, 10, "2026-09-21T10:00:00+00:00")]
    rows = summarize(records, since=None)
    assert rows[0].persona == "none"
    assert rows[0].total_output_bytes == 10


# --- cmd extraction ---------------------------------------------------------


def test_extract_cmd_skips_global_options_and_stops_at_command_flags():
    assert extract_cmd(["--persona", "host", "topic", "comment", "--id", "x"]) == "topic comment"
    assert extract_cmd(["--json", "work"]) == "work"
    assert extract_cmd(["--format", "yaml", "experiment", "show", "--id", "1"]) == "experiment show"
    assert extract_cmd(["usage", "summary", "--since", "2026-01-01"]) == "usage summary"


# --- counting stdout --------------------------------------------------------


def test_counting_stdout_counts_utf8_bytes_and_forwards():
    real = io.StringIO()
    proxy = CountingStdout(real)
    proxy.write("héllo")  # 5 chars, 6 utf-8 bytes
    proxy.writelines(["a", "b"])
    assert proxy.bytes_written == 6 + 2
    assert real.getvalue() == "hélloab"


def test_counting_stdout_forwards_attributes():
    real = io.StringIO()
    proxy = CountingStdout(real)
    assert proxy.encoding == real.encoding


# --- exit code mapping ------------------------------------------------------


def test_exit_code_from_exc_variants():
    assert exit_code_from_exc(None) == 0
    assert exit_code_from_exc(SystemExit(0)) == 0
    assert exit_code_from_exc(SystemExit(None)) == 0
    assert exit_code_from_exc(SystemExit(2)) == 2
    assert exit_code_from_exc(SystemExit("boom")) == 1
    assert exit_code_from_exc(RuntimeError("x")) == 1


# --- record_cli_call best-effort -------------------------------------------


def test_record_cli_call_writes_with_kind_from_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(KINDS_ENV, "my_open_experiments")
    record_cli_call(
        map_dir=tmp_path, persona="host", cmd="experiment complete",
        output_bytes=492, exit_code=0,
    )
    (row,) = list(read_records(tmp_path))
    assert row["persona"] == "host"
    assert row["kind"] == "my_open_experiments"
    assert row["cmd"] == "experiment complete"
    assert row["output_bytes"] == 492
    assert row["exit_code"] == 0
    assert parse_since(row["ts"])  # valid ISO


def test_record_cli_call_null_kind_when_no_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(KINDS_ENV, raising=False)
    record_cli_call(
        map_dir=tmp_path, persona=None, cmd="work", output_bytes=1, exit_code=0
    )
    (row,) = list(read_records(tmp_path))
    assert row["kind"] is None
    assert row["persona"] is None


def test_record_cli_call_swallows_failures(monkeypatch, tmp_path: Path):
    # map_dir 指向一个文件而非目录 → mkdir 失败，必须被吞掉（不影响 CLI）。
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    record_cli_call(
        map_dir=blocker, persona="host", cmd="work", output_bytes=1, exit_code=0
    )  # 不抛异常即通过


def test_record_cli_call_no_map_dir_is_noop():
    record_cli_call(
        map_dir=None, persona="host", cmd="work", output_bytes=1, exit_code=0
    )  # 不抛异常


# --- map usage summary command ---------------------------------------------


def _seed(map_dir: Path, rows: list[dict]) -> None:
    path = ledger_path(map_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_usage_summary_json_aggregates_window(tmp_path: Path, monkeypatch, runner):
    monkeypatch.setattr(usage_cmd, "resolve_map_dir", lambda: tmp_path)
    now = datetime.now(timezone.utc)
    inside = (now - timedelta(minutes=1)).isoformat()
    outside = (now - timedelta(days=1)).isoformat()
    _seed(tmp_path, [
        _rec("host", 100, outside),
        _rec("host", 200, inside, kind="my_open_experiments"),
        _rec("reviewer", 50, inside),
    ])
    since = (now - timedelta(hours=1)).isoformat()
    result = runner.invoke(app, ["usage", "summary", "--since", since, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout.strip())
    by_persona = {r["persona"]: r for r in payload}
    assert by_persona["host"] == {"persona": "host", "work_calls": 1, "total_output_bytes": 200}
    assert by_persona["reviewer"]["work_calls"] == 1


def test_usage_summary_rejects_bad_since(tmp_path: Path, monkeypatch, runner):
    monkeypatch.setattr(usage_cmd, "resolve_map_dir", lambda: tmp_path)
    result = runner.invoke(app, ["usage", "summary", "--since", "garbage"])
    assert result.exit_code == 2
    assert "invalid --since" in result.stderr


def test_usage_summary_no_map_dir_errors(monkeypatch, runner):
    monkeypatch.setattr(usage_cmd, "resolve_map_dir", lambda: None)
    result = runner.invoke(app, ["usage", "summary"])
    assert result.exit_code == 1
    assert "no .map/" in result.stderr
