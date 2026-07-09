"""experiments.lock_holder_experiment_id unique partial index (race PR1)

Goal
----
Enforce DB-level "at most one active holder" on the soft per-project
execution lock. The application-level ``_find_project_holder`` check in
``server.services.lock_service.acquire_experiment_lock`` has a TOCTOU
race: two concurrent acquires can both pass the "no holder" check and
both commit, leaving the project with two live holders. This partial
unique index makes that race detectable as ``IntegrityError`` at commit
time so the application can roll back and raise ``ConflictError``.

PG:    ``CREATE UNIQUE INDEX ... WHERE lock_holder_experiment_id IS NOT NULL``
SQLite/MySQL: 降级为现有普通索引(PR 计划里写明 PG-only;跨进程 waker 协调文档
              也只覆盖 PG 部署)。

Out of scope (在 PR2 / 其他实验处理)
------------------------------------
* Notification 表 UNIQUE(recipient_agent_id, group_key) — PR2
* simple-waker seen_group_keys per 周期 dedup — PR3

Verification
------------
* 本地 PG：``psql -c "EXPLAIN ANALYZE SELECT * FROM experiments WHERE
  lock_holder_experiment_id = '<uuid>'"`` 应走 ``Index Scan using
  uq_experiment_lock_holder_active``。
* fuzz：``tests/test_experiment_lock_fuzz.py`` 在多进程并发 acquire 场景下
  断言「总成功数 == 1」与「总 IntegrityError 数 == N-1」。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "experiments" not in inspector.get_table_names():
        return
    if bind.dialect.name != "postgresql":
        return  # PR 计划 PG-only；SQLite/MySQL 保留现有普通索引

    if _has_index(inspector, "experiments", "uq_experiment_lock_holder_active"):
        return  # idempotent guard

    op.create_index(
        "uq_experiment_lock_holder_active",
        "experiments",
        ["lock_holder_experiment_id"],
        unique=True,
        postgresql_where=sa.text("lock_holder_experiment_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_index(
        "uq_experiment_lock_holder_active",
        table_name="experiments",
    )
