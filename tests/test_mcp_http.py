import httpx
import pytest
from map_client.testing import MAPTestClientTransport
from map_mcp.config import MCPServerSettings
from map_mcp.server import build_server
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def test_mcp_settings_defaults():
    settings = MCPServerSettings.from_env()
    assert settings.transport == "stdio"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8080
    assert settings.path == "/mcp"


def test_mcp_settings_http(monkeypatch):
    monkeypatch.setenv("MAP_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MAP_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MAP_MCP_PORT", "9001")
    settings = MCPServerSettings.from_env()
    assert settings.transport == "streamable-http"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9001
    assert settings.url == "http://127.0.0.1:9001/mcp"


@pytest.mark.asyncio
async def test_mcp_streamable_http_bearer_auth(client, agent_token):
    _, token = agent_token
    mcp = build_server(None, api_url="http://test", transport=MAPTestClientTransport(client))
    app = mcp.streamable_http_app()
    session_manager = mcp.session_manager

    async with session_manager.run():
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {token}"}
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080", headers=headers) as http_client,
            streamable_http_client("http://127.0.0.1:8080/mcp", http_client=http_client) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert any(tool.name == "get_me" for tool in tools.tools)
            result = await session.call_tool("get_me", {})
            assert result.structuredContent is not None
            assert result.structuredContent["name"] == "test-agent"
