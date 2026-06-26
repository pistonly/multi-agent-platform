import pytest
from mcp.server.fastmcp.exceptions import ToolError

from map_client.testing import MAPTestClientTransport
from map_mcp.server import build_server

AGENT_TOOLS = {
    "get_me",
    "get_project_status",
    "list_project_status_versions",
    "get_project_status_version",
    "list_experiments",
    "get_experiment",
    "create_experiment",
    "submit_for_review",
    "approve_experiment",
    "withdraw_from_review",
    "cancel_experiment",
    "start_experiment",
    "complete_experiment",
    "list_plans",
    "get_plan",
    "revise_plan",
    "create_review",
    "list_reviews",
    "update_review_item",
    "create_comment",
    "list_comments",
    "create_log",
    "list_logs",
    "get_audit_history",
    "get_todos",
    "list_notifications",
    "mark_notification_read",
    "mark_all_notifications_read",
    "list_topics",
    "get_topic",
    "create_topic",
    "create_topic_comment",
    "close_topic",
    "reopen_topic",
}

ADMIN_TOOLS = {
    "get_global_status",
    "list_projects",
    "get_project",
    "create_project",
    "revise_project_status",
}

ALL_TOOLS = AGENT_TOOLS | ADMIN_TOOLS


@pytest.mark.asyncio
async def test_mcp_agent_tool_surface(map_client):
    mcp = build_server(map_client)
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == ALL_TOOLS


@pytest.mark.asyncio
async def test_mcp_admin_tool_surface(admin_map_client):
    mcp = build_server(admin_map_client)
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == ALL_TOOLS


@pytest.mark.asyncio
async def test_mcp_get_me(map_client, project):
    mcp = build_server(map_client)
    _, payload = await mcp.call_tool("get_me", {})
    assert payload["name"] == "test-agent"
    assert payload["project_key"] == project["project_key"]


@pytest.mark.asyncio
async def test_mcp_get_me_with_token_param(client, map_client, reviewer):
    mcp = build_server(map_client)
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")
    _, payload = await mcp.call_tool("get_me", {"token": reviewer_token})
    assert payload["name"] == "reviewer-agent"


@pytest.mark.asyncio
async def test_mcp_gateway_mode_requires_token(client, agent_token):
    _, creator_token = agent_token
    mcp = build_server(None, api_url="http://test", transport=MAPTestClientTransport(client))

    with pytest.raises(ToolError, match="Authentication required"):
        await mcp.call_tool("get_me", {})

    _, payload = await mcp.call_tool("get_me", {"token": creator_token})
    assert payload["name"] == "test-agent"


@pytest.mark.asyncio
async def test_mcp_multi_agent_collaboration(client, map_client, reviewer):
    mcp = build_server(map_client)
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")

    _, exp_payload = await mcp.call_tool(
        "create_experiment",
        {
            "title": "Multi-agent MCP experiment",
            "plan_content_md": "# Plan\nCollaboration via per-call tokens.",
            "submit_for_review": True,
        },
    )
    experiment_id = exp_payload["id"]

    _, review_payload = await mcp.call_tool(
        "create_review",
        {
            "experiment_id": experiment_id,
            "reasonable_items": ["Clear scope"],
            "unreasonable_items": ["Add acceptance criteria"],
            "token": reviewer_token,
        },
    )
    assert review_payload["experiment_id"] == experiment_id
    assert len(review_payload["items"]) == 2


@pytest.mark.asyncio
async def test_mcp_project_and_experiment_flow(map_client, project):
    mcp = build_server(map_client)

    _, exp_payload = await mcp.call_tool(
        "create_experiment",
        {
            "title": "MCP experiment",
            "plan_content_md": "# Plan\nRun MCP integration test.",
            "submit_for_review": True,
        },
    )
    experiment_id = exp_payload["id"]
    assert exp_payload["phase"] == "review"

    _, status_payload = await mcp.call_tool("get_project_status", {})
    assert status_payload["experiment_counts_by_phase"]["review"] >= 1

    resources = await mcp.list_resource_templates()
    uris = {resource.uriTemplate for resource in resources}
    assert "map://experiment/{experiment_id}" in uris
    assert "map://project/{project_key}/current-status" in uris
    assert "map://project/{project_id}/status" in uris

    content = await mcp.read_resource(f"map://experiment/{experiment_id}")
    assert "MCP experiment" in content[0].content


@pytest.mark.asyncio
async def test_mcp_create_experiment_without_project_id(map_client):
    mcp = build_server(map_client)
    _, payload = await mcp.call_tool(
        "create_experiment",
        {
            "title": "Bound project experiment",
            "plan_content_md": "# Plan\nNo project_id needed.",
        },
    )
    assert payload["title"] == "Bound project experiment"


@pytest.mark.asyncio
async def test_mcp_admin_tool_rejects_agent_token(map_client, reviewer):
    mcp = build_server(map_client)
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")

    with pytest.raises(ToolError, match="Admin role required"):
        await mcp.call_tool("list_projects", {"token": reviewer_token})


@pytest.mark.asyncio
async def test_mcp_topic_flow(map_client):
    mcp = build_server(map_client)
    _, topic = await mcp.call_tool("create_topic", {"title": "MCP 话题", "description": "讨论"})
    assert topic["status"] == "open"
    topic_id = topic["id"]

    _, comment = await mcp.call_tool(
        "create_topic_comment", {"topic_id": topic_id, "body": "一条讨论"}
    )
    assert comment["body"] == "一条讨论"

    _, detail = await mcp.call_tool("get_topic", {"topic_id": topic_id})
    assert detail["comment_count"] == 1
    assert detail["comments"][0]["body"] == "一条讨论"

    _, closed = await mcp.call_tool("close_topic", {"topic_id": topic_id})
    assert closed["status"] == "closed"

    _, opened = await mcp.call_tool("reopen_topic", {"topic_id": topic_id})
    assert opened["status"] == "open"


@pytest.mark.asyncio
async def test_mcp_notifications(client, map_client, reviewer):
    mcp = build_server(map_client)
    reviewer_token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")

    _, exp_payload = await mcp.call_tool(
        "create_experiment",
        {
            "title": "MCP notify",
            "plan_content_md": "# Plan",
        },
    )
    await mcp.call_tool("submit_for_review", {"experiment_id": exp_payload["id"]})

    _, inbox = await mcp.call_tool("list_notifications", {"token": reviewer_token})
    assert inbox["unread_count"] >= 1
    notif = next(n for n in inbox["items"] if n["event"] == "experiment.phase_changed")

    _, read = await mcp.call_tool(
        "mark_notification_read",
        {"notification_id": notif["id"], "token": reviewer_token},
    )
    assert read["read_at"] is not None

    _, marked = await mcp.call_tool("mark_all_notifications_read", {"token": reviewer_token})
    assert marked["marked"] >= 0
