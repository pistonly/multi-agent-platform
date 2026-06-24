from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

TransportName = Literal["stdio", "streamable-http", "sse"]


@dataclass(frozen=True)
class MCPServerSettings:
    transport: TransportName
    host: str
    port: int
    path: str

    @classmethod
    def from_env(
        cls,
        *,
        transport: str | None = None,
        host: str | None = None,
        port: int | None = None,
        path: str | None = None,
    ) -> MCPServerSettings:
        resolved_transport = (transport or os.environ.get("MAP_MCP_TRANSPORT", "stdio")).strip()
        if resolved_transport not in ("stdio", "streamable-http", "sse"):
            raise ValueError("transport must be one of: stdio, streamable-http, sse")

        default_host = "127.0.0.1" if resolved_transport == "stdio" else "0.0.0.0"
        resolved_host = (host or os.environ.get("MAP_MCP_HOST", default_host)).strip()
        resolved_port = port or int(os.environ.get("MAP_MCP_PORT", "8080"))
        resolved_path = (path or os.environ.get("MAP_MCP_PATH", "/mcp")).strip() or "/mcp"
        if not resolved_path.startswith("/"):
            resolved_path = f"/{resolved_path}"

        return cls(
            transport=resolved_transport,  # type: ignore[arg-type]
            host=resolved_host,
            port=resolved_port,
            path=resolved_path,
        )

    @property
    def url(self) -> str:
        return f"http://{self.public_host}:{self.port}{self.path}"

    @property
    def public_host(self) -> str:
        return "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
