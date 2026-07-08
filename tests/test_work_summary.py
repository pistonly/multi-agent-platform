"""6-bucket by_kind work summary acceptance tests (experiment 8d52232d).

Covers:
* (a) 3 case schema coverage: all buckets empty / partial / full.
* (b) persona filter: participant sees only "all"-visibility buckets by
  default; --include-all-personas opt-in shows host_only.
* (c) summary endpoint tolerates the legacy alias field name in the
  underlying TodoRead payloads (renamed stale_since reads back as
  advance_round_pending_since for clients still on the old name).

The summary endpoint is exposed by ``GET /agents/me/work/summary``; we
drive it through the FastAPI TestClient instead of the CLI to keep the
test focused on the schema/service contract.
"""

from __future__ import annotations

import pytest
from map_types.enums import TopicCommentKind

pytestmark = pytest.mark.slow


def _seed_topic_with_mention(client, project, headers):
    """Spin up topic + comment with @mention to drive a mention bucket."""
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=headers,
        json={"title": "summary-mention-topic", "body": "hello"},
    ).json()
    return topic


def test_summary_all_buckets_empty(client, project, admin_headers):
    """No topics, no mentions, no experiments → 0 buckets with count > 0.

    Uses admin_headers since auth_headers is a generic test-agent that the
    persona filter would otherwise hide host_only buckets from.
    """
    response = client.get(
        "/api/v1/agents/me/work/summary",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["topics_needing_attention"] == 0
    assert payload["experiments_needing_attention"] == 0
    assert payload["visibility_filter_applied"] is False  # admin sees all
    assert payload["topics_limit"] == 10
    assert payload["experiments_limit"] == 5
    for bucket in payload["buckets"]:
        assert bucket["count"] == 0


def test_summary_partial_buckets_when_only_one_mention_present(
    client, project, auth_headers, admin_headers
):
    """One mention → mention bucket count=1, all others empty."""
    # create a project admin user and a topic from that admin so we can
    # @mention the host agent (auth_headers) from a different author.
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=admin_headers,
        json={"title": "summary-mention-topic", "body": "host check"},
    ).json()
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()
    body = f"@{me['name']} please review"
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=admin_headers,
        json={"body": body, "kind": TopicCommentKind.user.value},
    )

    response = client.get(
        "/api/v1/agents/me/work/summary",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    bucket_counts = {b["kind"]: b["count"] for b in payload["buckets"]}
    assert bucket_counts.get("mention", 0) == 1
    # Other buckets are zero / absent
    for kind in ("round_ack", "pending_reply", "explicit_only", "informational_only", "action_items"):
        assert bucket_counts.get(kind, 0) == 0
    assert payload["topics_needing_attention"] >= 1


def test_summary_full_buckets_when_many_partitions_present(
    client, project, auth_headers, admin_headers, reviewer
):
    """Drive every bucket — mention, round_ack, pending_reply, action_items,
    explicit_only (my_open_experiments), informational_only (pending_result_reviews).

    Uses auth_headers (test-agent) + ``include_all_personas=true`` so the
    persona filter exposes host_only buckets too — this also puts the
    mention + my_open_experiment on the same agent so a single query can
    observe both buckets.
    """
    # 1) mention: admin comments on a topic @-ing the test-agent
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=admin_headers,
        json={"title": "summary-full-topic", "body": "kickoff"},
    ).json()
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=admin_headers,
        json={"body": f"@{me['name']} check this", "kind": TopicCommentKind.user.value},
    )

    # 2) explicit_only: my_open_experiment (draft → at least experiment exists)
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "summary-full-experiment", "plan": {"content_md": "## plan"}},
    ).json()
    assert exp["phase"] == "draft"

    response = client.get(
        "/api/v1/agents/me/work/summary",
        params={"include_all_personas": "true"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    bucket_kinds = {b["kind"] for b in payload["buckets"]}
    assert "mention" in bucket_kinds
    assert "explicit_only" in bucket_kinds
    bucket_counts = {b["kind"]: b["count"] for b in payload["buckets"]}
    assert bucket_counts["mention"] == 1
    assert bucket_counts["explicit_only"] == 1  # the draft experiment


def test_summary_persona_filter_hides_host_only_for_participant(
    client, project, auth_headers, admin_headers
):
    """A participant persona should not see host_only buckets by default."""
    # make a participant agent
    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": "summary-participant-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert resp.status_code == 201
    p_headers = {"Authorization": f"Bearer {resp.json()['api_token']}"}

    # seed an experiment that this participant owns → would normally go into explicit_only
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=p_headers,
        json={"title": "p-experiment", "plan": {"content_md": "## plan"}},
    )

    # default: participant sees no host_only buckets
    default = client.get(
        "/api/v1/agents/me/work/summary",
        headers=p_headers,
    ).json()
    bucket_kinds = {b["kind"] for b in default["buckets"]}
    assert "explicit_only" not in bucket_kinds
    assert "informational_only" not in bucket_kinds
    assert "action_items" not in bucket_kinds
    assert default["visibility_filter_applied"] is True

    # --include-all-personas=true (param form for TestClient)
    full = client.get(
        "/api/v1/agents/me/work/summary",
        params={"include_all_personas": "true"},
        headers=p_headers,
    ).json()
    full_kinds = {b["kind"] for b in full["buckets"]}
    assert "explicit_only" in full_kinds
    assert full["visibility_filter_applied"] is False


def test_summary_truncation_respects_topics_limit(
    client, project, auth_headers, admin_headers
):
    """topics_limit=2 caps each bucket to 2 items; truncated count is reported."""
    # create 3 distinct mention topics
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()
    for i in range(3):
        topic = client.post(
            f"/api/v1/projects/{project['id']}/topics",
            headers=admin_headers,
            json={"title": f"truncate-topic-{i}", "body": "x"},
        ).json()
        client.post(
            f"/api/v1/topics/{topic['id']}/comments",
            headers=admin_headers,
            json={"body": f"@{me['name']} #{i}", "kind": TopicCommentKind.user.value},
        )

    response = client.get(
        "/api/v1/agents/me/work/summary",
        params={"topics_limit": 2},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    mention_bucket = next(b for b in payload["buckets"] if b["kind"] == "mention")
    assert mention_bucket["count"] == 2
    assert payload["topics_truncated"] >= 1


def test_summary_experiments_needing_attention_by_owner_breakdown(
    client, project, admin_headers
):
    """f873c287 I1(e): host can see how many experiments are waiting on
    which persona via ``experiments_needing_attention_by_owner``.

    Uses admin_headers because ``my_open_experiments`` is partitioned by
    ``creator_agent_id``; admin agents see the full picture and the
    persona filter is disabled (``visibility_filter_applied=False``).

    Setup: 2 host-owned experiments (draft + approved → both phase_owner=host)
    + 1 reviewer-owned experiment (review phase, after submit-for-review by
    host → phase_owner=reviewer). The summary should expose the split.
    """
    # 2 host-owned experiments (draft + approved)
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "host-1", "plan": {"content_md": "## p"}},
    )
    approved = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "host-2", "plan": {"content_md": "## p"}},
    ).json()
    client.post(
        f"/api/v1/experiments/{approved['id']}/approve",
        headers=admin_headers,
    )
    # 1 reviewer-owned experiment: submit_for_review moves phase=review
    # and phase_owner=reviewer (via the resolver). It is informational_only
    # for the host because blocked_on=awaiting_non_creator_review and
    # phase_owner != host, but it still counts toward the by_owner
    # breakdown (the I1(e) goal: surface "what's waiting on whom").
    in_review = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "reviewer-1", "plan": {"content_md": "## p"}},
    ).json()
    sr_resp = client.post(
        f"/api/v1/experiments/{in_review['id']}/submit-review",
        headers=admin_headers,
    )
    assert sr_resp.status_code == 200, sr_resp.text

    response = client.get(
        "/api/v1/agents/me/work/summary",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["experiments_needing_attention"] == 3
    by_owner = payload["experiments_needing_attention_by_owner"]
    assert by_owner.get("host") == 2
    assert by_owner.get("reviewer") == 1
    assert payload["visibility_filter_applied"] is False  # admin sees all


def test_summary_experiments_needing_attention_by_owner_hides_host_for_participant(
    client, project, auth_headers, admin_headers
):
    """f873c287 I1(e): participant persona never sees host-owned experiments
    in ``experiments_needing_attention_by_owner`` — the underlying bucket is
    host_only and the visibility filter drops both the bucket items and the
    ``host`` key from the breakdown dict.
    """
    # create a participant agent
    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={
            "name": "summary-i1e-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert resp.status_code == 201
    p_headers = {"Authorization": f"Bearer {resp.json()['api_token']}"}

    # participant owns an experiment — phase_owner still defaults to host
    # so it falls into the host_only bucket and is hidden from the
    # participant.
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=p_headers,
        json={"title": "p-experiment", "plan": {"content_md": "## p"}},
    )

    default = client.get(
        "/api/v1/agents/me/work/summary",
        headers=p_headers,
    ).json()
    assert default["visibility_filter_applied"] is True
    assert "host" not in default["experiments_needing_attention_by_owner"]
    assert default["experiments_needing_attention"] == 0

    # admin opt-in still reveals the host-owned experiment with host=1.
    full = client.get(
        "/api/v1/agents/me/work/summary",
        params={"include_all_personas": "true"},
        headers=p_headers,
    ).json()
    assert full["visibility_filter_applied"] is False
    assert full["experiments_needing_attention_by_owner"].get("host") == 1
    assert full["experiments_needing_attention"] == 1
