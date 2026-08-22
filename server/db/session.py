from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect
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


def verify_schema_matches_models() -> None:
    """校验已存在的表结构与当前 ORM 模型一致（启动时调用）。

    ``init_db`` 的 ``create_all`` 只补缺失的表，**不会给既有表补列**。
    旧版本创建的库直接启动时，此前要到第一个请求才以 500 +
    SQLAlchemy traceback 暴露（如 ``no such column: projects.content_root``）。
    这里在服务就绪前逐表比对列名，落后时抛出带修复指引的
    ``RuntimeError``。全新库（表刚由当前模型建出）天然通过。
    """
    checker = inspect(engine)
    problems: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if not checker.has_table(table_name):
            continue
        db_columns = {col["name"] for col in checker.get_columns(table_name)}
        missing = [c.name for c in table.columns if c.name not in db_columns]
        if missing:
            problems.append(f"{table_name} 缺少列: {', '.join(missing)}")
    if problems:
        raise RuntimeError(
            "数据库 schema 落后于当前代码版本（"
            + "；".join(problems)
            + "）。请先备份数据并执行迁移后再启动，例如: "
            "cp data/map.db data/map.db.bak && alembic upgrade head"
        )
