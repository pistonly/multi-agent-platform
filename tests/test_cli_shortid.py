"""v0.12 M54B: short-id (UUID prefix) resolution for ``--id`` (E2 fix).

Pins the acceptance from ``docs/prd/v0.12.md`` M54B / plan v2 r1:

* ``--id`` accepts a full UUID (unchanged behavior, no list query) or a
  >=8-hex-digit prefix; the prefix is resolved through the **DB-layer**
  query ``list_experiments_page(id_prefix=...)`` (server:
  ``CAST(id AS CHAR) LIKE '<prefix>%'``).
* unique hit → the command operates on the resolved full UUID (the
  evidence_keys literal: ``--id <短id>`` output carries the full uuid);
* no hit → usage error (exit 2) naming the prefix;
* multiple hits → usage error (exit 2) listing candidate short forms;
* anything malformed (<8 hex, non-hex) → usage error (exit 2).

Integration strategy mirrors ``tests/test_cli_subcommand_format.py``: a
programmable ``httpx`` transport stub serves a fixed experiment set so
the CliRunner exercises the real command → SDK → request chain without a
live server.
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import typer
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from cli.shortid import normalize_uuid_like, resolve_ref

PROJECT_ID = uuid.uuid4()
_TS = "2026-01-01T00:00:00Z"


def _exp(eid: uuid.UUID, title: str = "experiment") -> dict:
    """Minimal valid ``ExperimentSummaryRead`` payload for the stub."""
    return {
        "id": str(eid),
        "project_id": str(PROJECT_ID),
        "creator_agent_id": str(uuid.uuid4()),
        "title": title,
        "description": None,
        "phase": "running",
        "current_plan_version": 1,
        "created_at": _TS,
        "updated_at": _TS,
    }


class StubTransport:
    """Serves a fixed experiment list; records every request.

    * ``GET /projects/{pid}/experiments?id_prefix=X`` → prefix-filtered
      page (JSON body + ``X-Total-Count`` header, matching the real API);
    * ``GET /experiments/{uuid}`` → detail or 404;
    * anything else → 404 (tests only use the above two paths).
    """

    def __init__(self, experiments: list[dict]) -> None:
        self.experiments = experiments
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        url = str(request.url)
        self.calls.append((method, url))
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        if method == "GET" and parsed.path.endswith("/experiments"):
            prefix = params.get("id_prefix", [""])[0]
            hits = [
                e
                for e in self.experiments
                if e["id"].replace("-", "").startswith(prefix)
            ]
            return httpx.Response(
                200,
                headers={"X-Total-Count": str(len(hits))},
                content=json.dumps(hits).encode(),
            )

        if method == "GET" and "/experiments/" in parsed.path:
            exp_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            for e in self.experiments:
                if e["id"] == exp_id:
                    return httpx.Response(200, json=e)
            return httpx.Response(404, json={"detail": "Experiment not found"})

        return httpx.Response(404, json={"detail": f"unstubbed {method} {url}"})


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_env(monkeypatch):
    """Install a StubTransport + fixed project resolution + auth env."""
    transport = StubTransport([])
    monkeypatch.setattr(cli_main, "_transport", transport)
    monkeypatch.setattr(
        cli_main, "_resolve_project", lambda client, p, k: PROJECT_ID
    )
    monkeypatch.setenv("MAP_TOKEN", "fake")
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    return transport


# ---- unit: resolve_ref ------------------------------------------------------


def test_resolve_ref_full_uuid_passthrough():
    uid = uuid.uuid4()
    assert resolve_ref(str(uid), kind="experiment", matcher=lambda p: []) == uid
    assert resolve_ref(uid, kind="experiment", matcher=lambda p: []) == uid


def test_resolve_ref_32hex_passthrough():
    uid = uuid.uuid4()
    raw = uid.hex
    assert len(raw) == 32
    assert resolve_ref(raw, kind="experiment", matcher=lambda p: []) == uid


def test_resolve_ref_unique_prefix_resolves():
    uid = uuid.uuid4()
    matcher = lambda p: [(uid, "solo")]  # noqa: E731
    assert resolve_ref(uid.hex[:8], kind="experiment", matcher=matcher) == uid


def test_resolve_ref_no_match_is_usage_error():
    with pytest.raises(typer.BadParameter) as exc:
        resolve_ref("deadbeef", kind="experiment", matcher=lambda p: [])
    assert "no experiment matches id prefix 'deadbeef'" in str(exc.value)


def test_resolve_ref_ambiguous_lists_candidates():
    a, b = uuid.uuid4(), uuid.uuid4()
    matcher = lambda p: [(a, "alpha"), (b, "beta")]  # noqa: E731
    with pytest.raises(typer.BadParameter) as exc:
        resolve_ref("abcd1234", kind="experiment", matcher=matcher)
    msg = str(exc.value)
    assert "ambiguous (2 matches)" in msg
    assert "alpha" in msg and "beta" in msg
    assert a.hex[:12] in msg and b.hex[:12] in msg
    assert "lengthen the prefix" in msg


def test_resolve_ref_rejects_short_and_non_hex():
    matcher = lambda p: [(uuid.uuid4(), "x")]  # noqa: E731
    for bad in ("abc1234", "zzzzzzzz", "123456789g"):
        with pytest.raises(typer.BadParameter) as exc:
            resolve_ref(bad, kind="experiment", matcher=matcher)
        assert "invalid experiment id" in str(exc.value)


def test_resolve_ref_normalizes_case_and_dashes():
    uid = uuid.uuid4()
    matcher = lambda p: [(uid, "solo")]  # noqa: E731
    raw = uid.hex[:8].upper()
    assert resolve_ref(raw, kind="experiment", matcher=matcher) == uid


# ---- unit: normalize_uuid_like ---------------------------------------------


def test_normalize_uuid_like_matrix():
    uid = uuid.uuid4()
    assert normalize_uuid_like(uid) == uid
    assert normalize_uuid_like(str(uid)) == uid
    assert normalize_uuid_like(uid.hex) == uid
    assert normalize_uuid_like(uid.hex[:8]) is None  # short prefix → None
    assert normalize_uuid_like("not-a-uuid") is None
    assert normalize_uuid_like(None) is None
    assert normalize_uuid_like("") is None


# ---- integration: CLI command chain ----------------------------------------


def test_show_short_prefix_lists_then_gets_full_uuid(stub_env, runner):
    """evidence_keys literal: ``--id <8-hex>`` resolves and the output
    carries the full UUID; the prefix went out as ``id_prefix=``."""
    uid = uuid.uuid4()
    stub_env.experiments = [_exp(uid, title="m54b target")]

    result = runner.invoke(app, ["experiment", "show", "--id", uid.hex[:8]])
    assert result.exit_code == 0, result.output
    assert str(uid) in result.output

    # request chain: prefix list query → detail fetch of the FULL uuid
    methods_urls = stub_env.calls
    list_call = next(u for m, u in methods_urls if "id_prefix=" in u)
    assert f"id_prefix={uid.hex[:8]}" in list_call
    detail_call = next(u for m, u in methods_urls if "/experiments/" in u)
    assert str(uid) in detail_call


def test_show_full_uuid_skips_list_query(stub_env, runner):
    """A full UUID never triggers the prefix-resolution list call."""
    uid = uuid.uuid4()
    stub_env.experiments = [_exp(uid)]

    result = runner.invoke(app, ["experiment", "show", "--id", str(uid)])
    assert result.exit_code == 0, result.output
    assert all("id_prefix=" not in u for _, u in stub_env.calls)


def test_show_ambiguous_prefix_exits_2_with_candidates(stub_env, runner):
    a, b, shared = _ambiguous_pair()
    stub_env.experiments = [_exp(a, "alpha"), _exp(b, "beta")]

    result = runner.invoke(app, ["experiment", "show", "--id", shared])
    assert result.exit_code == 2
    stderr = result.stderr or ""
    assert "ambiguous" in stderr
    assert "alpha" in stderr and "beta" in stderr


def test_show_unknown_prefix_exits_2(stub_env, runner):
    stub_env.experiments = [_exp(uuid.uuid4())]
    missing = _unused_prefix(stub_env.experiments)

    result = runner.invoke(app, ["experiment", "show", "--id", missing])
    assert result.exit_code == 2
    stderr = result.stderr or ""
    assert f"no experiment matches id prefix '{missing}'" in stderr


def test_show_malformed_id_exits_2_without_requests(stub_env, runner):
    result = runner.invoke(app, ["experiment", "show", "--id", "zz12"])
    assert result.exit_code == 2
    assert "invalid experiment id" in (result.stderr or "")
    assert stub_env.calls == []


def test_nested_lock_release_accepts_short_prefix(stub_env, runner):
    """Nested sub-apps (experiment → lock) resolve prefixes too."""
    uid = uuid.uuid4()
    stub_env.experiments = [_exp(uid)]

    # release hits an unstubbed path (404) — what matters is that the
    # request targeted the resolved FULL uuid, and no crash happened.
    result = runner.invoke(
        app, ["experiment", "lock", "release", "--id", uid.hex[:8]]
    )
    assert result.exit_code != 0
    target = next(u for _, u in stub_env.calls if "/experiments/" in u)
    assert str(uid) in target


def test_short_prefix_with_sub_format_json(stub_env, runner):
    """M54A × M54B: subcommand ``--format json`` + short id → JSON data
    envelope whose ``id`` is the resolved full UUID."""
    uid = uuid.uuid4()
    stub_env.experiments = [_exp(uid, "json target")]

    result = runner.invoke(
        app,
        ["experiment", "show", "--format", "json", "--id", uid.hex[:8]],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("{"))
    envelope = json.loads("\n".join(lines[start:]))
    assert envelope["ok"] is True
    assert envelope["data"]["id"] == str(uid)


# ---- helpers ----------------------------------------------------------------


def _ambiguous_pair() -> tuple[uuid.UUID, uuid.UUID, str]:
    """Two UUIDs sharing an 8-hex prefix (random pairs essentially never
    collide on 8 hex digits, so construct them explicitly)."""
    shared = uuid.uuid4().hex[:8]
    a = uuid.UUID(f"{shared}{uuid.uuid4().hex[8:]}")
    b = uuid.UUID(f"{shared}{uuid.uuid4().hex[8:]}")
    assert a != b
    return a, b, shared


def _unused_prefix(existing: list[dict]) -> str:
    """An 8-hex prefix matching none of ``existing``."""
    for _ in range(200):
        cand = uuid.uuid4().hex[:8]
        if not any(e["id"].replace("-", "").startswith(cand) for e in existing):
            return cand
    pytest.skip("could not generate unused prefix")
