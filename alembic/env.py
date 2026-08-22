from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from server.config import get_settings
from server.db.base import Base
from server.domain import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig 默认 True 会把当时已存在的
    # 所有未在 alembic.ini 列出的 logger 标记 disabled（进程级、不可逆）。
    # 测试进程中 run_lock 等业务 logger 先于迁移创建时会被静默禁用，
    # 导致后续 caplog 断言拿不到记录（test_experiment_lock_unit 全量失败）。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
