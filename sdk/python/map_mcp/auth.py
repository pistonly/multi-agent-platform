from __future__ import annotations

from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_request_bearer: ContextVar[str | None] = ContextVar("map_mcp_request_bearer", default=None)


def get_request_bearer() -> str | None:
    return _request_bearer.get()


def parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return None
    return credentials.strip() or None


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Extract Authorization: Bearer from each HTTP request into a context variable."""

    async def dispatch(self, request: Request, call_next) -> Response:
        token = parse_bearer_token(request.headers.get("authorization"))
        reset = _request_bearer.set(token)
        try:
            return await call_next(request)
        finally:
            _request_bearer.reset(reset)
