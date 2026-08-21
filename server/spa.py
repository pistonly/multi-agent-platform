"""Serve the bundled React SPA from the same origin as the MAP API.

The Vite production build (``web/dist``) is copied to ``server/web_dist`` by
``scripts/sync-web-dist.sh`` and shipped inside the Python wheel. ``map-server``
then serves ``/`` and client-side routes as ``index.html``, while ``/api`` /
``/health`` / OpenAPI stay on FastAPI.

When the bundled files are missing (editable checkout without sync), the API
starts as usual and unknown paths keep returning JSON 404s.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

logger = logging.getLogger(__name__)

DEFAULT_WEB_DIST = Path(__file__).resolve().parent / "web_dist"

# First path segment of routes that must never be rewritten to index.html.
_RESERVED_ROOTS = frozenset(
    {
        "api",
        "health",
        "docs",
        "redoc",
        "openapi.json",
        "assets",
    }
)

_INDEX_CACHE_HEADERS = {"Cache-Control": "no-cache"}


def is_reserved_path(path: str) -> bool:
    """Return True for API / docs / hashed-asset paths (not SPA fallback)."""
    stripped = path.lstrip("/")
    if not stripped:
        return False
    root = stripped.split("/", 1)[0]
    return root in _RESERVED_ROOTS


def resolve_web_dist(web_dist: str | Path | None = None) -> Path | None:
    """Return a dist directory that contains ``index.html``, or None.

    An explicit ``web_dist`` / ``MAP_WEB_DIST`` is not a fallback chain: if
    the path is set but ``index.html`` is missing, return None so the API
    still starts without silently serving a different tree.
    """
    if web_dist is not None:
        candidate = Path(web_dist).resolve()
        if (candidate / "index.html").is_file():
            return candidate
        return None
    default = DEFAULT_WEB_DIST.resolve()
    if (default / "index.html").is_file():
        return default
    return None


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Mount hashed assets and rewrite unmatched GET/HEAD 404s to index.html.

    Must be registered **before** CORSMiddleware so the fallback ``FileResponse``
    still receives CORS headers (last ``add_middleware`` runs first).
    """
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="web_assets")

    index = dist / "index.html"

    @app.middleware("http")
    async def spa_fallback(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if response.status_code != 404:
            return response
        if request.method not in {"GET", "HEAD"}:
            return response
        if is_reserved_path(request.url.path):
            return response
        return FileResponse(index, headers=_INDEX_CACHE_HEADERS)

    logger.info("Serving web UI from %s", dist)
