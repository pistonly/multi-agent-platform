"""Alembic 049: project.content_root + fs_freshness_sla_seconds round-trip."""

from __future__ import annotations

import os

from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect

from alembic import command
from server.config import get_settings


def _make_alembic_config(db_url: str) -> AlembicConfig:
    os.environ["MAP_DATABASE_URL"] = db_url
    get_settings.cache_clear()
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_049_upgrade_downgrade_upgrade(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'p1.db'}"
    cfg = _make_alembic_config(db_url)
    command.upgrade(cfg, "049")
    engine = create_engine(db_url)
    columns = {c["name"] for c in inspect(engine).get_columns("projects")}
    assert "content_root" in columns
    assert "fs_freshness_sla_seconds" in columns
    command.downgrade(cfg, "048")
    columns = {c["name"] for c in inspect(engine).get_columns("projects")}
    assert "content_root" not in columns
    assert "fs_freshness_sla_seconds" not in columns
    command.upgrade(cfg, "head")
    columns = {c["name"] for c in inspect(engine).get_columns("projects")}
    assert "content_root" in columns
    engine.dispose()
