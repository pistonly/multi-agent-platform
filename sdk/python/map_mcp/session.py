from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from map_client.client import MAPClient
from map_mcp.context import AgentContext

if TYPE_CHECKING:
    import httpx

_TOKEN_PARAM_HELP = (
    "Agent API token. Pass to act as a specific registered agent in this call. "
    "Omit to use MAP_TOKEN from the environment (if configured)."
)


def token_param() -> str:
    """Shared description for the optional token tool parameter."""
    return _TOKEN_PARAM_HELP


class ClientResolver:
    """Resolve MAPClient + AgentContext from an optional per-call token."""

    def __init__(
        self,
        api_url: str,
        default_client: MAPClient | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._default_client = default_client
        self._default_ctx = AgentContext.from_client(default_client) if default_client else None
        if transport is not None:
            self._transport = transport
        elif default_client is not None:
            self._transport = default_client._transport
        else:
            self._transport = None

    @property
    def default_ctx(self) -> AgentContext | None:
        return self._default_ctx

    @contextmanager
    def use(self, token: str | None) -> Iterator[tuple[MAPClient, AgentContext]]:
        if token:
            client = MAPClient(self.api_url, token, transport=self._transport)
            try:
                yield client, AgentContext.from_client(client)
            finally:
                client.close()
            return
        if self._default_client is None or self._default_ctx is None:
            raise ValueError("token is required when MAP_TOKEN is not configured on the MCP server")
        yield self._default_client, self._default_ctx
