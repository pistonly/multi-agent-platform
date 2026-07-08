"""``resolve-item --status`` SDK + CLI registry contract (experiment b95894db I1(b)).

Pins the **SDK call contract** for plan (b):

1. ``MAPClient.update_review_item(item_id, status)`` issues a PATCH against
   ``/review-items/{item_id}`` with a JSON body that serialises the
   ``ReviewItemStatus`` enum as its string value (not the enum object) —
   guards against the ``model_dump(mode="json")`` regression that bit
   experiment 216b6a81 v1.
2. The CLI command is registered with ``--status`` so the help surface
   documents it — guards against accidental removal.
3. ``MAPClient.update_review_item`` signature declares ``status`` as
   ``ReviewItemStatus`` so static type checkers (mypy / pyright) catch
   callers passing raw strings.

These are unit-level contract tests: no FastAPI TestClient, no state
machine. End-to-end ``open → rebutted`` business semantics stay in
plan (c) (review_item.state migration).
"""

from __future__ import annotations

import inspect
import json
import uuid

import httpx
import pytest
from map_client.client import MAPClient
from map_types.enums import ReviewItemStatus
from typer.testing import CliRunner

from cli.main import app

pytestmark = pytest.mark.slow


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class _CaptureTransport(httpx.BaseTransport):
    """httpx transport that records outbound requests and echoes the
    request's status back so the SDK round-trip parses cleanly."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = json.loads(request.content.decode("utf-8"))
        body = json.dumps(
            {
                "id": request.url.path.rsplit("/", 1)[-1],
                "review_id": "00000000-0000-0000-0000-000000000000",
                "kind": "unreasonable",
                "content": "stub",
                "status": payload["status"],
                "created_at": "2026-07-08T00:00:00Z",
                "updated_at": "2026-07-08T00:00:00Z",
            }
        )
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=body.encode("utf-8"),
        )


def _make_client(transport: httpx.BaseTransport) -> MAPClient:
    return MAPClient(
        base_url="http://test",
        token="dummy-token",
        transport=transport,
    )


def test_sdk_update_review_item_serializes_status_rebutted_as_json_string():
    """``MAPClient.update_review_item`` must PATCH /review-items/{id} with
    ``{"status": "rebutted"}`` (string), not the enum object — this is the
    exact failure mode that bit experiment 216b6a81."""
    transport = _CaptureTransport()
    item_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = _make_client(transport)

    with client:
        result = client.update_review_item(item_id, ReviewItemStatus.rebutted)

    assert len(transport.requests) == 1, "exactly one outbound request"
    request = transport.requests[0]
    assert request.method == "PATCH"
    assert request.url.path.endswith(f"/review-items/{item_id}")

    body = json.loads(request.content.decode("utf-8"))
    assert body == {"status": "rebutted"}, (
        f"payload must serialise enum to its string value, got {body!r}"
    )

    assert result.status == ReviewItemStatus.rebutted


def test_sdk_update_review_item_serializes_status_resolved_as_json_string():
    """Same contract for ``resolved`` — the backward-compat default path."""
    transport = _CaptureTransport()
    item_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    client = _make_client(transport)

    with client:
        result = client.update_review_item(item_id, ReviewItemStatus.resolved)

    body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert body == {"status": "resolved"}
    assert result.status == ReviewItemStatus.resolved


def test_sdk_update_review_item_signature_declares_reviewitemstatus():
    """Static contract: callers passing raw strings (instead of the enum)
    should fail type-check. Guards against accidental signature drift.

    Note: ``from __future__ import annotations`` turns annotations into
    strings, so we compare the string form rather than the resolved object.
    """
    sig = inspect.signature(MAPClient.update_review_item)
    params = sig.parameters
    assert "item_id" in params
    assert "status" in params
    assert params["status"].annotation == "ReviewItemStatus"


def test_cli_resolve_item_help_registers_status_option(runner):
    """The CLI registry must surface ``--status`` so users can discover the
    ``rebutted`` alternative without reading source code."""
    result = runner.invoke(app, ["experiment", "review", "resolve-item", "--help"])
    assert result.exit_code == 0, result.output
    assert "--status" in result.output
    # Both values documented so the help text teaches the semantics
    assert "resolved" in result.output
    assert "rebutted" in result.output
    # Default value present (typer may wrap it across lines; collapse whitespace)
    collapsed = " ".join(result.output.split())
    assert "[default: resolved]" in collapsed
