from __future__ import annotations

import typer

from map_mcp.config import MCPServerSettings

cli = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    help="Multi-Agent Platform MCP server (stdio or HTTP).",
)


def _ensure_mcp_installed() -> None:
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
    except ImportError as exc:
        typer.echo(
            "MCP support requires the 'mcp' package. Install with: pip install -e \".[mcp]\"",
            err=True,
        )
        raise typer.Exit(code=1) from exc


def _run_server(settings: MCPServerSettings) -> None:
    from map_client import MAPClient
    from map_client.config import load_config

    from map_mcp.server import build_server

    cfg = load_config()
    api_url = cfg["api_url"]
    # HTTP: identity comes from Authorization Bearer per connection.
    # stdio: use MAP_TOKEN from env/config as the default client.
    token = cfg.get("token")
    client = MAPClient(api_url, token) if token and settings.transport == "stdio" else None
    mcp = build_server(client, api_url=api_url, host=settings.host, port=settings.port, path=settings.path)

    if settings.transport == "stdio":
        mcp.run(transport="stdio")
        return

    if settings.transport == "streamable-http":
        typer.echo(f"MAP MCP listening at {settings.url}", err=True)
        mcp.run(transport="streamable-http")
        return

    typer.echo(f"MAP MCP SSE listening at http://{settings.public_host}:{settings.port}/sse", err=True)
    mcp.run(transport="sse")


@cli.callback()
def main(
    ctx: typer.Context,
    transport: str | None = typer.Option(
        None,
        "--transport",
        "-t",
        help="Transport: stdio (default), streamable-http, or sse",
    ),
    host: str | None = typer.Option(None, "--host", help="Bind host for HTTP transports"),
    port: int | None = typer.Option(None, "--port", "-p", help="Bind port for HTTP transports"),
    path: str | None = typer.Option(None, "--path", help="HTTP MCP endpoint path (default: /mcp)"),
) -> None:
    """Run the MAP MCP server."""
    if ctx.invoked_subcommand is not None:
        return

    _ensure_mcp_installed()
    _run_server(MCPServerSettings.from_env(transport=transport, host=host, port=port, path=path))


if __name__ == "__main__":
    cli()
