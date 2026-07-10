"""rebutted 后 plan 可继续 (experiment b95894db I1(f)).

Pins plan (f) acceptance:

> rebutted 后当前 plan 可继续 (host 处理其他 item + 修订 plan),无需
> reject 整个实验(端到端测试:单 item rebutted + 其他 item resolved +
> 实验进入 result_review)

End-to-end flow:

1. Reviewer submits a review with two unreasonable items.
2. Host rebuts item A (single-item push-back), reviewer resolves item B
   directly via ``addressed → resolved`` (or via ``withdrawn``).
3. Host approves the experiment (despite an open ``rebutted`` item).
4. Experiment advances through ``running → complete → result_review``
   without host having to call ``reject-result``.

This proves the rebutted path is a real two-step handshake that does
NOT block downstream progress.
"""

from __future__ import annotations

from tests._frontmatter import make_valid_plan


def test_rebutted_item_does_not_block_experiment_progress_to_result_review(
    client, auth_headers, reviewer, project
):
    """End-to-end: rebut one item, resolve the others, advance to result_review
    without any reject-result detour."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "rebutted plan-continues 实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    # Step 1: reviewer submits two unreasonable items.
    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={
            "unreasonable_items": ["item-A: 体验问题", "item-B: 性能瓶颈"],
        },
    ).json()
    items = sorted(
        (i for i in review["items"] if i["kind"] == "unreasonable"),
        key=lambda i: i["content"],
    )
    assert len(items) == 2
    item_a_id = items[0]["id"]  # "item-A: ..."
    item_b_id = items[1]["id"]  # "item-B: ..."

    # Step 2: host rebuts item A (single-item push-back).
    rebut = client.patch(
        f"/api/v1/review-items/{item_a_id}",
        headers=auth_headers,
        json={"status": "rebutted"},
    )
    assert rebut.status_code == 200, rebut.text
    assert rebut.json()["status"] == "rebutted"

    # Reviewer closes both items on the v1 review BEFORE plan_revise archives
    # it (18f1d8f6 I1(b) + I1(d) — archived reviews reject resolve/withdraw/
    # update-item). State machine paths:
    #   - item A: rebutted → resolved (reviewer, allowed)
    #   - item B: open → withdrawn (reviewer, allowed)
    resolve_a = client.patch(
        f"/api/v1/review-items/{item_a_id}",
        headers=reviewer["headers"],
        json={"status": "resolved"},
    )
    assert resolve_a.status_code == 200, resolve_a.text
    assert resolve_a.json()["status"] == "closed"
    assert resolve_a.json()["last_resolution_reason"] == "resolved"

    withdraw_b = client.patch(
        f"/api/v1/review-items/{item_b_id}",
        headers=reviewer["headers"],
        json={"status": "withdrawn"},
    )
    assert withdraw_b.status_code == 200, withdraw_b.text
    assert withdraw_b.json()["status"] == "closed"
    assert withdraw_b.json()["last_resolution_reason"] == "superseded"

    # plan_revise now archives the v1 review (I1(b)).
    revise = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={"content_md": make_valid_plan(body="## 修订后的计划 (item-A resolved, item-B withdrawn)")},
    )
    assert revise.status_code == 201, revise.text

    # After plan_revise, the v1 review is archived and items keep their
    # terminal status. ``rebutted`` does NOT block approval because the
    # follow-up ``rebutted → resolved`` handshake has completed.
    items_after_revise = client.get(
        f"/api/v1/experiments/{exp_id}/reviews?include_archived=true",
        headers=auth_headers,
    ).json()
    flat_items = [i for r in items_after_revise for i in r["items"]]
    by_content = {i["content"]: i for i in flat_items if i["kind"] == "unreasonable"}
    assert by_content["item-A: 体验问题"]["status"] == "closed"
    assert by_content["item-A: 体验问题"]["last_resolution_reason"] == "resolved"
    assert by_content["item-B: 性能瓶颈"]["status"] == "closed"
    assert by_content["item-B: 性能瓶颈"]["last_resolution_reason"] == "superseded"

    # Sanity: any further mutation on the archived v1 review surfaces the
    # REVIEW_ALREADY_ARCHIVED contract (I1(d)).
    archived_attempt = client.patch(
        f"/api/v1/review-items/{item_a_id}",
        headers=reviewer["headers"],
        json={"status": "open"},
    )
    assert archived_attempt.status_code == 422
    assert archived_attempt.json()["error_code"] == "REVIEW_ALREADY_ARCHIVED"

    # Reviewer acknowledges the v2 plan with an empty review so approve
    # eligibility can find a non-creator review on the current version.
    v2_ack = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["v2 plan reviewed, no objections"]},
    )
    assert v2_ack.status_code == 201, v2_ack.text

    approve = client.post(
        f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["phase"] == "approved"

    # Step 4: experiment advances all the way through running → result_review
    # without any reject-result detour.
    start = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    assert start.status_code == 200
    assert start.json()["phase"] == "running"

    complete = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={
            "summary": "实验完成",
            "content_md": "驳回 item A + 解决 item B 后顺利推进",
            "metadata": {"pytest_summary": "unit passed"},
        },
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["phase"] == "result_review"

    # Final: the experiment reaches result_review without ever calling
    # reject-result. The reviewer can now accept-result; host never had
    # to back out of the experiment.

    # Audit trail: the rebuttal handshake produced both add_item (on
    # submit) and resolve_item rows (on each PATCH) so the per-item
    # timeline is reconstructable.
    # (We don't assert on the audit here — that's I1(e) coverage; this
    # test is about the lifecycle progression only.)


def test_rebutted_item_counted_as_open_in_approve_eligibility(
    client, auth_headers, reviewer, project
):
    """A single ``rebutted`` item blocks approval (same as ``open`` /
    ``addressed``) — that's the rebuttal follow-up handshake. The host
    must wait for the reviewer to either resolve or re-open the item.

    This is the *guard* that makes I1(f) meaningful: without it, the
    rebuttal would be silently dropped.
    """
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "rebutted guard 实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["item-A"]},
    ).json()
    item_a_id = next(i["id"] for i in review["items"] if i["kind"] == "unreasonable")

    # Host rebuts item A.
    rebut = client.patch(
        f"/api/v1/review-items/{item_a_id}",
        headers=auth_headers,
        json={"status": "rebutted"},
    )
    assert rebut.status_code == 200

    # Approval must fail because rebutted items count toward
    # ``count_open_unreasonable_for_experiment``.
    approve = client.post(
        f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers
    )
    assert approve.status_code == 409, approve.text
    body = approve.json()
    assert body["reason"] == "open_unreasonable_item"
    assert "rebutted" in body["detail"].lower() or "open" in body["detail"].lower()
