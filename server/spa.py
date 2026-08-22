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

# Vite 产物文件名带内容哈希，可安全长缓存（发新版即换文件名）。
_ASSETS_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


class _HashedAssetFiles(StaticFiles):
    """``/assets`` 静态服务：为内容寻址资源补 immutable 长缓存头。"""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers.update(_ASSETS_CACHE_HEADERS)
        return response


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


def _dist_root_file(dist: Path, url_path: str) -> Path | None:
    """dist 根级静态文件（favicon/manifest 等）查找；穿越路径返回 None。

    仅接受 resolve 后仍位于 dist 内的常规文件；命中者不带内容哈希，
    回退调用方需使用协商缓存（no-cache）。
    """
    relative = url_path.lstrip("/")
    if not relative:
        return None
    try:
        candidate = (dist / relative).resolve()
        candidate.relative_to(dist)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Mount hashed assets and rewrite unmatched GET/HEAD 404s to index.html.

    Must be registered **before** CORSMiddleware so the fallback ``FileResponse``
    still receives CORS headers (last ``add_middleware`` runs first).
    """
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", _HashedAssetFiles(directory=assets), name="web_assets")

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
        # dist 根级静态文件（favicon.ico / manifest 等）：命中直接返回，
        # 否则浏览器对自动探测资源的请求会拿到 200 的 HTML。
        root_file = _dist_root_file(dist, request.url.path)
        if root_file is not None:
            return FileResponse(root_file, headers=_INDEX_CACHE_HEADERS)
        return FileResponse(index, headers=_INDEX_CACHE_HEADERS)

    logger.info("Serving web UI from %s", dist)
