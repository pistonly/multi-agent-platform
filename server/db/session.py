from collections.abc import Generator

from sqlalchemy import create_engine, event
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


def init_db() -> None:
    """Create tables from metadata.

    Schema 增量变更统一走 alembic migrations（``alembic/versions/``）。
    本函数只在 dev / 测试环境使用 ``create_all`` 创建缺失的表；不再做
    ``ALTER TABLE ADD COLUMN`` 自愈——历史遗留的 ``_ENSURE_COLUMNS`` /
    ``_backfill_*`` 已分别由 migration 023 / 025 / 027 覆盖。
    生产环境请用 ``alembic upgrade head``。
    """
    Base.metadata.create_all(bind=engine)
