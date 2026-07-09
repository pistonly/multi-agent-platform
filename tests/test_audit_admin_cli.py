"""CLI ``map audit list --kind review_item_mutation --experiment <id>`` (I1(g)).

Pins plan (g) acceptance from the CLI side: the registered Typer command
parses ``--kind`` and ``--experiment`` correctly, hits the SDK with the
right filters, and renders a ``# audit_log`` summary header line followed
by YAML rows.

Tested via ``typer.testing.CliRunner`` + the ``MAPTestClientTransport``
fixture used by sibling CLI tests — no live server required.
"""

from __future__ import annotations

import re

import pytest
import yaml
from map_client.testing import MAPTestClientTransport
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from tests._frontmatter import make_valid_plan


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_admin_cli(monkeypatch, client, admin_headers):
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_ADMIN_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


@pytest.fixture
def experiment_with_audit_rows(client, auth_headers, reviewer, project) -> dict:
    """Stand up an experiment that produces ``review_item.mutation`` rows."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "cli audit filter 实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["cli-A", "cli-B"]},
    ).json()
    item_a_id = next(
        i["id"] for i in review["items"] if i["kind"] == "unreasonable"
        and i["content"] == "cli-A"
    )
    # PATCH so we get a resolve_item row in addition to add_item rows.
    resp = client.patch(
        f"/api/v1/review-items/{item_a_id}",
        headers=reviewer["headers"],
        json={"status": "withdrawn"},
    )
    assert resp.status_code == 200
    return {"experiment_id": exp_id, "item_a_id": item_a_id}


def test_cli_audit_list_renders_summary_and_rows(
    runner: CliRunner,
    patched_admin_cli,
    experiment_with_audit_rows,
    tmp_path,
):
    """``map audit list --kind review_item.mutation --experiment <id>``
    prints ``# audit_log total=… returned=…`` header then YAML rows."""
    ctx = experiment_with_audit_rows

    result = runner.invoke(
        app,
        [
            "--project-root",
            str(tmp_path),
            "audit",
            "list",
            "--kind",
            "review_item.mutation",
            "--experiment",
            ctx["experiment_id"],
            "--page-size",
            "100",
        ],
    )
    assert result.exit_code == 0, result.output

    header = re.search(
        r"# audit_log total=(\d+) returned=(\d+) "
        r"kind=(.+?) experiment=(.+)$",
        result.output,
        re.MULTILINE,
    )
    assert header, f"missing summary header in:\n{result.output}"
    total = int(header.group(1))
    returned = int(header.group(2))
    assert total >= 3
    assert returned >= 3
    assert header.group(3) == "review_item.mutation"
    assert header.group(4) == ctx["experiment_id"]

    # Body lines after the header parse as YAML.
    body = result.output.split("\n", 1)[1]
    rows = yaml.safe_load(body)
    assert isinstance(rows, list)
    assert rows, "expected YAML rows"
    assert all(r["action"] == "review_item.mutation" for r in rows)
    assert all(
        (r.get("payload_json") or {}).get("experiment_id") == ctx["experiment_id"]
        for r in rows
    )


def test_cli_audit_list_without_filters_lists_all(
    runner: CliRunner,
    patched_admin_cli,
    experiment_with_audit_rows,
    tmp_path,
):
    """``map audit list`` (no filters) still works — returns all kinds
    paginated, with the kind/experiment fields set to ``*``."""
    result = runner.invoke(
        app,
        ["--project-root", str(tmp_path), "audit", "list"],
    )
    assert result.exit_code == 0, result.output

    header = re.search(r"# audit_log total=(\d+) returned=(\d+)", result.output)
    assert header, f"missing summary header in:\n{result.output}"
    assert int(header.group(1)) >= 3
    # ``kind`` and ``experiment`` slots default to ``*`` when not provided.
    assert "kind=*" in result.output
    assert "experiment=*" in result.output


def test_cli_audit_list_filters_must_be_well_formed(
    runner: CliRunner,
    patched_admin_cli,
    tmp_path,
):
    """``--experiment`` must be a valid UUID; otherwise Typer rejects."""
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(tmp_path),
            "audit",
            "list",
            "--experiment",
            "not-a-uuid",
        ],
    )
    assert result.exit_code != 0
    assert "invalid" in result.output.lower() or "uuid" in result.output.lower()


def test_cli_audit_list_admin_rejection_surfaces_error(
    runner: CliRunner,
    monkeypatch,
    tmp_path,
):
    """A server-side admin rejection (403) still surfaces the error code
    and exits non-zero — exercises ``audit_list``'s inline MAPHTTPError
    handler on the admin-token path.

    We stub ``admin_client`` so the rejection happens before reaching the
    test DB. The server-side ``require_admin`` guard itself is covered by
    ``test_admin_audit_filter_requires_admin`` in ``test_audit_admin_filter``.
    """
    monkeypatch.setenv("MAP_ADMIN_TOKEN", "fake-admin-token")
    monkeypatch.setenv("MAP_API_URL", "http://test")

    from map_client.exceptions import MAPHTTPError

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        def list_audit_global(self, **_kwargs):
            raise MAPHTTPError(403, "Admin role required")

        def close(self):
            pass

    monkeypatch.setattr(cli_main, "admin_client", lambda *a, **_kw: _StubClient())

    result = runner.invoke(
        app,
        [
            "--project-root",
            str(tmp_path),
            "audit",
            "list",
            "--kind",
            "review_item.mutation",
        ],
    )
    assert result.exit_code != 0
    assert "403" in result.output or "forbidden" in result.output.lower()


def test_cli_audit_list_missing_admin_api_url(
    runner: CliRunner,
    monkeypatch,
    tmp_path,
):
    """With no ``.map/`` project and no ``MAP_API_URL``, the command fails
    fast with a clear message. api_url is resolved before the token, so
    this is deterministic regardless of ``~/.map/admin.yaml`` on the host.
    """
    monkeypatch.delenv("MAP_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("MAP_API_URL", raising=False)

    result = runner.invoke(
        app,
        ["--project-root", str(tmp_path), "audit", "list"],
    )
    assert result.exit_code != 0
    assert "API URL" in result.output or "api_url" in result.output.lower()
