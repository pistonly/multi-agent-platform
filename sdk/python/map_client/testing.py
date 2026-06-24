"""Test utilities for in-process SDK/CLI tests."""

from __future__ import annotations

import httpx
from starlette.testclient import TestClient


class MAPTestClientTransport(httpx.BaseTransport):
    def __init__(self, test_client: TestClient) -> None:
        self._client = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query.decode()}"
        response = self._client.request(
            request.method,
            path,
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
        )
