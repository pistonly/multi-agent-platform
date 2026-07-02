from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from server.config import get_settings
from server.db.base import Base

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Idempotent self-healing for columns added after create_all() was first run on a
# pre-existing database. ``create_all`` only creates missing tables; it does not
# widen columns on an already-existing table. New columns introduced in v0.x+ are
# listed here so the API container picks them up on next boot without a manual
# ``ALTER TABLE`` step in dev / staging.
_ENSURE_COLUMNS: dict[str, dict[str, str]] = {
    "topic_action_items": {
        "category": "VARCHAR(32)",
        "cancel_reason": "TEXT",
        "wake_count": "INTEGER NOT NULL DEFAULT 0",
        "last_woken_at": "VARCHAR(64)",
        "first_open_at": "VARCHAR(64)",
        "stale_at": "VARCHAR(64)",
    },
    "notifications": {
        "category": "VARCHAR(16) NOT NULL DEFAULT 'digest'",
        "group_key": "VARCHAR(512)",
        "wake_version": "INTEGER NOT NULL DEFAULT 1",
        "event_count": "INTEGER NOT NULL DEFAULT 1",
        "first_event_at": "VARCHAR(64)",
        "last_event_at": "VARCHAR(64)",
        "updated_at": "VARCHAR(64)",
    },
}


def _ensure_extra_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ENSURE_COLUMNS.items():
            existing = {col["name"] for col in inspector.get_columns(table)}
            for column_name, ddl_type in columns.items():
                if column_name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {ddl_type}"))


def _backfill_action_item_first_open_at() -> None:
    """Backfill first_open_at for existing open action_items.

    New action_items created after the column was added will have first_open_at
    populated by the service layer (reopen / create paths in I3). For pre-existing
    rows we treat created_at as the first-open timestamp so the waker can compute
    T+24h / T+72h escalation windows without a data backfill migration.
    """
    inspector = inspect(engine)
    if "topic_action_items" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("topic_action_items")}
    if "first_open_at" not in columns or "created_at" not in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE topic_action_items "
                "SET first_open_at = created_at "
                "WHERE first_open_at IS NULL AND status = 'open'"
            )
        )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_extra_columns()
    _backfill_action_item_first_open_at()
