"""I1 (207d7c4b / A1): log 白名单放宽 draft/review/approved。

放宽前 draft/review/approved 写日志被 422 拒绝(立项/评审期审计被迫走 FS 旁路);
放宽后日志条目自带 phase 快照、只增不改,draft 起即可直接写平台。
"""
from __future__ import annotations

from tests._frontmatter import make_valid_plan


def _log_body(summary: str) -> dict:
    return {"summary": summary, "content_md": f"# log {summary}"}


def test_log_allowed_from_draft_through_approved(client, auth_headers, reviewer, project):
    """draft → review → approved 三阶段均应能 201 写入日志且时间线连续。"""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "phase whitelist I1",
            "plan": {"content_md": make_valid_plan(body="# p")},
        },
    ).json()
    exp_id = exp["id"]

    # draft → log 201 (放宽前此处 422)
    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json=_log_body("draft 审计日志"),
    )
    assert resp.status_code == 201, resp.text

    # review → log 201
    client.post(f"/api/v1/experiments/{exp_id}/submit-review", headers=auth_headers)
    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json=_log_body("review 评审日志"),
    )
    assert resp.status_code == 201, resp.text

    # approved → log 201
    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer["headers"],
                json={"status": "resolved"},
            )
    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json=_log_body("approved 待执行日志"),
    )
    assert resp.status_code == 201, resp.text

    # 时间线连续:同一实验日志按 created_at 升序,summary 三连对得上
    timeline = client.get(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
    ).json()
    summaries = [entry["summary"] for entry in timeline]
    assert summaries == ["draft 审计日志", "review 评审日志", "approved 待执行日志"]


def test_log_still_rejected_after_cancel(client, auth_headers, reviewer, project):
    """cancelled 终态仍拒绝追加日志(白名单保留的边界)。"""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "cancel gate I1",
            "plan": {"content_md": make_valid_plan(body="# p")},
        },
    ).json()
    exp_id = exp["id"]

    # draft → cancel: draft 阶段经 cancel 进入 cancelled 终态
    cancel = client.post(f"/api/v1/experiments/{exp_id}/cancel", headers=auth_headers)
    assert cancel.status_code == 200, cancel.text

    resp = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json=_log_body("cancelled 后应拒"),
    )
    assert resp.status_code == 422, resp.text
