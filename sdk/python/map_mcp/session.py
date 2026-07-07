from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from map_client.client import MAPClient

from map_mcp.auth import get_request_bearer
from map_mcp.context import AgentContext

if TYPE_CHECKING:
    import httpx

_TOKEN_PARAM_HELP = (
    "Deprecated. Prefer Authorization: Bearer <MAP agent token> on the MCP HTTP connection, "
    "or MAP_TOKEN in env for stdio. Overrides transport auth when set."
)


def token_param() -> str:
    """Shared description for the optional token tool parameter."""
    return _TOKEN_PARAM_HELP


class ClientResolver:
    """Resolve MAPClient + AgentContext from transport Bearer, stdio default, or per-call token."""

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

    def _resolve_token(self, token: str | None) -> str | None:
        if token:
            return token
        bearer = get_request_bearer()
        if bearer:
            return bearer
        if self._default_client is not None:
            return self._default_client.token
        return None

    @contextmanager
    def use(self, token: str | None) -> Iterator[tuple[MAPClient, AgentContext]]:
        effective = self._resolve_token(token)
        if not effective:
            raise ValueError(
                "Authentication required: set Authorization: Bearer <MAP agent token> "
                "(HTTP) or MAP_TOKEN (stdio)"
            )
        if (
            self._default_client is not None
            and effective == self._default_client.token
            and self._default_ctx is not None
        ):
            yield self._default_client, self._default_ctx
            return
        client = MAPClient(self.api_url, effective, transport=self._transport)
        try:
            yield client, AgentContext.from_client(client)
        finally:
            client.close()
