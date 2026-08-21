"""map-server serves the bundled SPA from the same origin as /api."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from server.spa import is_reserved_path, resolve_web_dist


def _write_spa(dist: Path) -> Path:
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>MAP</title></head>"
        "<body><div id='root'></div>"
        "<script type='module' src='/assets/app.js'></script></body></html>\n",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
    return dist


def test_reserved_path_roots() -> None:
    assert is_reserved_path("/api/v1/agents") is True
    assert is_reserved_path("health") is True
    assert is_reserved_path("/docs") is True
    assert is_reserved_path("/openapi.json") is True
    assert is_reserved_path("/assets/index.js") is True
    assert is_reserved_path("/") is False
    assert is_reserved_path("/topics/demo") is False
    assert is_reserved_path("/settings") is False


def test_resolve_web_dist_missing(tmp_path: Path) -> None:
    assert resolve_web_dist(tmp_path) is None


def test_resolve_web_dist_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = _write_spa(tmp_path / "web_dist")
    monkeypatch.setattr("server.spa.DEFAULT_WEB_DIST", dist)
    assert resolve_web_dist(None) == dist.resolve()


def test_spa_index_and_client_routes(tmp_path: Path) -> None:
    dist = _write_spa(tmp_path / "web_dist")
    app = create_app(init_db_on_startup=False, serve_web=True, web_dist=dist)
    client = TestClient(app)

    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "MAP" in index.text
    assert index.headers.get("cache-control") == "no-cache"

    topic = client.get("/topics/demo-slug")
    assert topic.status_code == 200
    assert "text/html" in topic.headers["content-type"]
    assert "MAP" in topic.text

    settings = client.get("/settings")
    assert settings.status_code == 200
    assert "MAP" in settings.text


def test_openapi_and_docs_not_swallowed(tmp_path: Path) -> None:
    dist = _write_spa(tmp_path / "web_dist")
    app = create_app(init_db_on_startup=False, serve_web=True, web_dist=dist)
    client = TestClient(app)

    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert spec.headers["content-type"].startswith("application/json")
    assert spec.json()["info"]["title"] == "Multi-Agent Platform"

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower() or "openapi" in docs.text.lower()


def test_spa_does_not_swallow_api_or_health(tmp_path: Path) -> None:
    dist = _write_spa(tmp_path / "web_dist")
    app = create_app(init_db_on_startup=False, serve_web=True, web_dist=dist)
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    missing = client.get("/api/v1/definitely-not-a-route")
    assert missing.status_code == 404
    assert "application/json" in missing.headers["content-type"]
    assert "MAP" not in missing.text


def test_spa_serves_hashed_assets_and_keeps_asset_404(tmp_path: Path) -> None:
    dist = _write_spa(tmp_path / "web_dist")
    app = create_app(init_db_on_startup=False, serve_web=True, web_dist=dist)
    client = TestClient(app)

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log('ok')" in asset.text

    missing = client.get("/assets/does-not-exist.js")
    assert missing.status_code == 404
    assert "MAP" not in missing.text


def test_serve_web_false_skips_spa(tmp_path: Path) -> None:
    dist = _write_spa(tmp_path / "web_dist")
    app = create_app(init_db_on_startup=False, serve_web=False, web_dist=dist)
    client = TestClient(app)
    assert client.get("/").status_code == 404
    assert client.get("/topics/demo").status_code == 404


def test_missing_dist_is_api_only(tmp_path: Path) -> None:
    app = create_app(init_db_on_startup=False, serve_web=True, web_dist=tmp_path)
    client = TestClient(app)
    assert client.get("/").status_code == 404
    assert client.get("/health").json() == {"status": "ok"}


def test_pyproject_declares_web_dist_package_data() -> None:
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "web_dist/**" in text
