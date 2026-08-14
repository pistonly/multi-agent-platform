"""Local SQLite cache for MAP project data — Plan A (read-only sync).

This module provides a lightweight local cache that stores JSON snapshots
of topics, experiments, and decisions pulled from a remote MAP server.
The cache lives at ``.map/cache.db`` and enables offline browsing of
project history.

Architecture::

    Remote MAP Server                  Local .map/cache.db
    ┌──────────────────┐               ┌────────────────────┐
    │  PostgreSQL /     │  ── pull ──>  │  sync_meta          │
    │  SQLite           │   (JSON)      │  cached_topics      │
    │                   │               │  cached_experiments │
    └──────────────────┘               └────────────────────┘

Design choices:
- JSON blob storage (not full ORM) — keeps the cache schema-agnostic
  and immune to server-side model changes
- Upsert by UUID — repeated pulls update in place
- ``sync_meta`` tracks last-pull timestamp per project for delta sync
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class SyncMeta(Base):
    """Tracks last-sync timestamp per project."""

    __tablename__ = "sync_meta"

    project_id = Column(String(36), primary_key=True)  # UUID as string
    project_key = Column(String(64), nullable=False)
    last_pull_at = Column(DateTime(timezone=True), nullable=False)
    topic_count = Column(String(10), default="0")
    experiment_count = Column(String(10), default="0")


class CachedTopic(Base):
    """A topic snapshot stored as JSON blob."""

    __tablename__ = "cached_topics"

    id = Column(String(36), primary_key=True)  # UUID as string
    project_id = Column(String(36), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    status = Column(String(32), nullable=False)
    data_json = Column(Text, nullable=False)  # Full TopicRead as JSON
    pulled_at = Column(DateTime(timezone=True), nullable=False)


class CachedExperiment(Base):
    """An experiment snapshot stored as JSON blob."""

    __tablename__ = "cached_experiments"

    id = Column(String(36), primary_key=True)  # UUID as string
    project_id = Column(String(36), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    phase = Column(String(32), nullable=False)
    data_json = Column(Text, nullable=False)  # Full ExperimentBundleRead as JSON
    pulled_at = Column(DateTime(timezone=True), nullable=False)


def get_cache_path(map_dir: Path) -> Path:
    """Return the cache database path inside ``.map/``."""
    return map_dir / "cache.db"


def init_cache(db_path: Path) -> Session:
    """Initialize (or open) the local cache database and return a session."""
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return SessionLocal()


def pull_project_to_cache(
    session: Session,
    client: Any,  # MAPClient
    project_id: uuid.UUID,
    project_key: str,
    *,
    include_archived: bool = True,
) -> dict[str, int]:
    """Pull all topics and experiments from remote server into local cache.

    Returns a dict with ``topics``, ``experiments`` counts.
    """
    now = datetime.now(timezone.utc)
    pid_str = str(project_id)

    # --- Pull topics ---
    topic_count = 0
    page = 1
    while True:
        batch = client.list_topics(
            project_id,
            page=page,
            page_size=100,
            include_archived=include_archived,
        )
        if not batch:
            break
        for summary in batch:
            topic = client.get_topic(summary.id)
            topic_json = topic.model_dump(mode="json") if hasattr(topic, "model_dump") else _to_dict(topic)
            # Upsert
            existing = session.get(CachedTopic, str(topic.id))
            if existing:
                existing.title = topic.title
                existing.status = str(topic.status.value if hasattr(topic.status, "value") else topic.status)
                existing.data_json = json.dumps(topic_json, default=str, ensure_ascii=False)
                existing.pulled_at = now
            else:
                session.add(CachedTopic(
                    id=str(topic.id),
                    project_id=pid_str,
                    title=topic.title,
                    status=str(topic.status.value if hasattr(topic.status, "value") else topic.status),
                    data_json=json.dumps(topic_json, default=str, ensure_ascii=False),
                    pulled_at=now,
                ))
            topic_count += 1
        if len(batch) < 100:
            break
        page += 1

    # --- Pull experiments ---
    experiment_count = 0
    page = 1
    while True:
        batch = client.list_experiments(
            project_id,
            page=page,
            page_size=100,
            include_archived=include_archived,
        )
        if not batch:
            break
        for summary in batch:
            bundle = client.get_experiment_bundle(summary.id)
            bundle_json = bundle.model_dump(mode="json") if hasattr(bundle, "model_dump") else _to_dict(bundle)
            exp = bundle.experiment if hasattr(bundle, "experiment") else bundle
            phase_val = str(exp.phase.value if hasattr(exp.phase, "value") else exp.phase)
            # Upsert
            existing = session.get(CachedExperiment, str(exp.id))
            if existing:
                existing.title = exp.title
                existing.phase = phase_val
                existing.data_json = json.dumps(bundle_json, default=str, ensure_ascii=False)
                existing.pulled_at = now
            else:
                session.add(CachedExperiment(
                    id=str(exp.id),
                    project_id=pid_str,
                    title=exp.title,
                    phase=phase_val,
                    data_json=json.dumps(bundle_json, default=str, ensure_ascii=False),
                    pulled_at=now,
                ))
            experiment_count += 1
        if len(batch) < 100:
            break
        page += 1

    # --- Update sync_meta ---
    meta = session.get(SyncMeta, pid_str)
    if meta:
        meta.last_pull_at = now
        meta.topic_count = str(topic_count)
        meta.experiment_count = str(experiment_count)
        meta.project_key = project_key
    else:
        session.add(SyncMeta(
            project_id=pid_str,
            project_key=project_key,
            last_pull_at=now,
            topic_count=str(topic_count),
            experiment_count=str(experiment_count),
        ))

    session.commit()
    return {"topics": topic_count, "experiments": experiment_count}


def get_sync_status(session: Session, project_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Return sync status for all projects (or a specific one)."""
    query = session.query(SyncMeta)
    if project_id is not None:
        query = query.filter(SyncMeta.project_id == str(project_id))
    results = []
    for meta in query.all():
        results.append({
            "project_id": meta.project_id,
            "project_key": meta.project_key,
            "last_pull_at": meta.last_pull_at.isoformat() if meta.last_pull_at else None,
            "topic_count": int(meta.topic_count or "0"),
            "experiment_count": int(meta.experiment_count or "0"),
        })
    return results


def list_cached_topics(session: Session, project_id: uuid.UUID) -> list[dict[str, Any]]:
    """List cached topics (lightweight metadata, no JSON blob)."""
    rows = session.query(CachedTopic).filter(CachedTopic.project_id == str(project_id)).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "status": r.status,
            "pulled_at": r.pulled_at.isoformat() if r.pulled_at else None,
        }
        for r in rows
    ]


def get_cached_topic(session: Session, topic_id: uuid.UUID) -> dict[str, Any] | None:
    """Get a single cached topic with full JSON data."""
    row = session.get(CachedTopic, str(topic_id))
    if row is None:
        return None
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "data": json.loads(row.data_json) if row.data_json else None,
        "pulled_at": row.pulled_at.isoformat() if row.pulled_at else None,
    }


def _to_dict(obj: Any) -> dict[str, Any]:
    """Fallback serialization for non-Pydantic objects."""
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {"value": str(obj)}
