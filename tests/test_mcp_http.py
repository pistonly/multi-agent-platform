import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from map_mcp.config import MCPServerSettings
from map_mcp.server import build_server


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
async def test_mcp_streamable_http_get_me(map_client):
    mcp = build_server(map_client, host="127.0.0.1", port=8080, path="/mcp")
    app = mcp.streamable_http_app()
    session_manager = mcp.session_manager

    async with session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080") as http_client:
            async with streamable_http_client("http://127.0.0.1:8080/mcp", http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert any(tool.name == "get_me" for tool in tools.tools)
                    result = await session.call_tool("get_me", {})
                    assert result.structuredContent is not None
                    assert result.structuredContent["name"] == "test-agent"
