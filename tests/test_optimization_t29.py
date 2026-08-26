"""T29: wheel/sdist must ship the SPA and alembic; CI must unpack-check them."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.migrate import resolve_alembic_ini

_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_includes_alembic_and_web_dist() -> None:
    text = (_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "graft alembic" in text
    assert "include alembic.ini" in text
    assert "graft server/web_dist" in text
    assert "include scripts/map_build_backend.py" in text


def test_pyproject_declares_web_dist_and_migrate_package_data() -> None:
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"web_dist/**"' in text
    assert '"_migrate/**"' in text
    assert 'build-backend = "map_build_backend"' in text
    assert 'backend-path = ["scripts"]' in text


def test_alembic_ini_script_location_is_here_relative() -> None:
    text = (_ROOT / "alembic.ini").read_text(encoding="utf-8")
    assert "script_location = %(here)s/alembic" in text


def test_ci_packaging_job_unpacks_wheel() -> None:
    text = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-packaging.sh" in text
    assert "scripts/sync-web-dist.sh" in text


def test_resolve_alembic_ini_prefers_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ini = tmp_path / "alembic.ini"
    ini.write_text("[alembic]\nscript_location = %(here)s/alembic\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_alembic_ini() == ini.resolve()


def test_resolve_alembic_ini_falls_back_to_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    bundled = tmp_path / "fake_bundle" / "alembic.ini"
    bundled.parent.mkdir()
    bundled.write_text("[alembic]\n", encoding="utf-8")
    monkeypatch.setattr("server.migrate._BUNDLED_INI", bundled)
    assert resolve_alembic_ini() == bundled.resolve()


def test_resolve_alembic_ini_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("server.migrate._BUNDLED_INI", tmp_path / "nope.ini")
    with pytest.raises(FileNotFoundError, match="alembic.ini not found"):
        resolve_alembic_ini()
