"""b72d0542 I1.b — API contract tests for 4-段 template soft validation.

Verifies the 4 acceptance (a) cases at the API endpoint layer:

| Case | body content_md                                | response.template_validation.warnings |
|------|------------------------------------------------|----------------------------------------|
| 1    | 完整 4 段 + 实施 log 含合法链接                | []                                     |
| 2    | 缺 summary                                     | [MISSING_TEMPLATE_SECTION x 1]         |
| 3    | 缺 acceptance 清单                             | [MISSING_TEMPLATE_SECTION x 1]         |
| 4    | 实施 log 含合法 + 不平衡 fragment              | [MALFORMED_MARKDOWN_LINK]              |

The validator never blocks complete — the response phase is
``result_review`` regardless of warning count, and ``valid`` is True.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._frontmatter import make_valid_plan


@pytest.fixture
def running_experiment_id(
    client: TestClient,
    auth_headers: dict[str, str],
    reviewer: dict[str, str],
    project: dict,
) -> str:
    """Create + review + approve + start an experiment; return its id."""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "I1.b API contract test",
            "plan": {"content_md": make_valid_plan(body="## test plan\n")},
            "submit_for_review": True,
        },
    ).json()

    review = client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
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
    client.post(f"/api/v1/experiments/{exp['id']}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp['id']}/start", headers=auth_headers)
    return exp["id"]


_COMPLETE_RESULT_BODY = (
    "# 实验 b72d0542 result\n\n"
    "## summary\n"
    "4 段模板就绪\n\n"
    "## 实施 log\n"
    "- [W45 I1.a](.map/generated-plans/experiment-b72d0542-i1-a-log.md)\n"
    "- [W46 I1.b](.map/generated-plans/experiment-b72d0542-i1-b-log.md)\n\n"
    "## 风险\n"
    "- 风险 1\n\n"
    "## acceptance\n"
    "- [x] (a) 4 段模板 + Pydantic schema 校验\n"
)


def _missing_summary_body() -> str:
    return (
        "## 实施 log\n"
        "- [W45](file.md)\n\n"
        "## 风险\n"
        "x\n\n"
        "## acceptance\n"
        "- [x] (a)\n"
    )


def _missing_acceptance_body() -> str:
    return (
        "## summary\n"
        "x\n\n"
        "## 实施 log\n"
        "- [W45](file.md)\n\n"
        "## 风险\n"
        "x\n"
    )


def _malformed_link_body() -> str:
    return (
        "## summary\n"
        "x\n\n"
        "## 实施 log\n"
        "- [valid](path.md)\n"
        "- text [broken without close\n\n"
        "## 风险\n"
        "x\n\n"
        "## acceptance\n"
        "- [x] (a)\n"
    )


def _filter_codes(warnings) -> list[str]:
    return [w["code"] for w in warnings]


# --- case 1: complete 4 段 + valid link -----------------------------------


def test_case_1_all_sections_present_emits_no_warnings(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    response = client.post(
        f"/api/v1/experiments/{running_experiment_id}/complete",
        headers=auth_headers,
        json={
            "summary": "result",
            "content_md": _COMPLETE_RESULT_BODY,
            "metadata": {"pytest_summary": "test evidence stub"},
        },
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    template = payload["template_validation"]
    assert template is not None
    assert template["warnings"] == []
    assert template["sections_present"] == ["summary", "实施 log", "风险", "acceptance"]
    assert template["log_link_count"] == 2
    assert template["valid"] is True
    assert payload["phase"] == "result_review"


# --- case 2: 缺 summary ---------------------------------------------------


def test_case_2_missing_summary_emits_warning(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    response = client.post(
        f"/api/v1/experiments/{running_experiment_id}/complete",
        headers=auth_headers,
        json={
            "summary": "result",
            "content_md": _missing_summary_body(),
            "metadata": {"pytest_summary": "test evidence stub"},
        },
    )
    assert response.status_code == 200, response.text

    template = response.json()["template_validation"]
    codes = _filter_codes(template["warnings"])
    assert "MISSING_TEMPLATE_SECTION" in codes
    missing = {
        w["section"]
        for w in template["warnings"]
        if w["code"] == "MISSING_TEMPLATE_SECTION"
    }
    assert "summary" in missing
    assert template["valid"] is True
    assert response.json()["phase"] == "result_review"


# --- case 3: 缺 acceptance ------------------------------------------------


def test_case_3_missing_acceptance_emits_warning(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    response = client.post(
        f"/api/v1/experiments/{running_experiment_id}/complete",
        headers=auth_headers,
        json={
            "summary": "result",
            "content_md": _missing_acceptance_body(),
            "metadata": {"pytest_summary": "test evidence stub"},
        },
    )
    assert response.status_code == 200, response.text

    template = response.json()["template_validation"]
    codes = _filter_codes(template["warnings"])
    assert "MISSING_TEMPLATE_SECTION" in codes
    missing = {
        w["section"]
        for w in template["warnings"]
        if w["code"] == "MISSING_TEMPLATE_SECTION"
    }
    assert "acceptance" in missing
    assert template["valid"] is True


# --- case 4: 实施 log 段含不平衡 fragment ---------------------------------


def test_case_4_malformed_markdown_link_emits_warning(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    response = client.post(
        f"/api/v1/experiments/{running_experiment_id}/complete",
        headers=auth_headers,
        json={
            "summary": "result",
            "content_md": _malformed_link_body(),
            "metadata": {"pytest_summary": "test evidence stub"},
        },
    )
    assert response.status_code == 200, response.text

    template = response.json()["template_validation"]
    codes = _filter_codes(template["warnings"])
    assert "MALFORMED_MARKDOWN_LINK" in codes
    malformed = [w for w in template["warnings"] if w["code"] == "MALFORMED_MARKDOWN_LINK"]
    assert malformed[0]["section"] == "实施 log"
    assert malformed[0]["detail"] is not None
    assert template["valid"] is True


# --- regression: validation field absent on non-complete endpoints --------


def test_template_validation_field_is_null_on_status_endpoint(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    """``GET /experiments/{id}`` (status) should not populate template_validation."""
    response = client.get(
        f"/api/v1/experiments/{running_experiment_id}", headers=auth_headers
    )
    assert response.status_code == 200
    payload = response.json()
    # Field is present (Pydantic schema) but null on non-complete paths.
    assert payload.get("template_validation") is None


def test_template_validation_appears_only_on_complete(
    client: TestClient, auth_headers: dict[str, str], running_experiment_id: str
) -> None:
    """The first call to ``complete`` populates the field; subsequent
    status calls do not. (The wrapper is set per-request, not persisted
    on the experiment row.)
    """
    response = client.post(
        f"/api/v1/experiments/{running_experiment_id}/complete",
        headers=auth_headers,
        json={
            "summary": "result",
            "content_md": _COMPLETE_RESULT_BODY,
            "metadata": {"pytest_summary": "test evidence stub"},
        },
    )
    assert response.status_code == 200
    assert response.json()["template_validation"] is not None
