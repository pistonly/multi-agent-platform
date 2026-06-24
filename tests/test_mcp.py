import pytest

from map_mcp.server import build_server

AGENT_ONLY_TOOLS = {
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
}

ADMIN_ONLY_TOOLS = {
    "get_global_status",
    "list_projects",
    "get_project",
    "create_project",
    "revise_project_status",
}


@pytest.mark.asyncio
async def test_mcp_agent_tool_surface(map_client):
    mcp = build_server(map_client)
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == AGENT_ONLY_TOOLS
    assert not names & ADMIN_ONLY_TOOLS


@pytest.mark.asyncio
async def test_mcp_admin_tool_surface(admin_map_client):
    mcp = build_server(admin_map_client)
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert AGENT_ONLY_TOOLS <= names
    assert ADMIN_ONLY_TOOLS <= names


@pytest.mark.asyncio
async def test_mcp_get_me(map_client, project):
    mcp = build_server(map_client)
    _, payload = await mcp.call_tool("get_me", {})
    assert payload["name"] == "test-agent"
    assert payload["project_key"] == project["project_key"]


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
    assert "map://project/{project_id}/status" not in uris

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
