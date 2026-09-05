"""Migration manifest for DB → FS projection 存量迁移（实验 M2 I5：A5）。

设计要点（对应 host 风险提示：94+ 实验迁移量大，manifest 幂等 key 设计不当
重试会产生重复记录）：

- ``migration_runs`` —— 每次 manifest 跑 = 一行；phase 推进留 audit 链
- ``migration_manifest_items`` —— 每个待迁移 DB 实体 = 一行
  - idempotency_key = (project_id, kind, slug, content_hash) 的
    SHA-256 摘要；UPSERT on conflict 走 DB unique index 拦截重复
  - status: ``pending | in_flight | applied | failed | skipped``
  - attempts + last_error 用于 retry budget + 错误归因
  - in_flight 但 last_attempt_at > 30min → 视为 stale，下次跑自动重置
    为 pending（kill -9 / 进程僵死兜底）

本迁移只建表与索引；逻辑写在 ``server/services/migration_manifest_service.py``。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "054"
down_revision: str | None = "053"


def upgrade() -> None:
    op.create_table(
        "migration_runs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.CHAR(length=32),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("phase", sa.String(length=16), nullable=False),
        # scan | dry_run | execute | verify
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=True,
            comment="JSON：counts by status + error breakdown",
        ),
    )

    op.create_table(
        "migration_manifest_items",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.CHAR(length=32),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "run_id",
            sa.String(length=32),
            sa.ForeignKey("migration_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            comment="topic | experiment",
        ),
        sa.Column("slug", sa.String(length=512), nullable=False),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
            comment="规范化 SHA-256；server 复核 apply CAS",
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256(project_id|kind|slug|content_hash)；unique 防重",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
            comment="pending | in_flight | applied | failed | skipped",
        ),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "last_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_error",
            sa.Text(),
            nullable=True,
            comment="apply 失败的 error_code + detail 摘要",
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_migration_manifest_idempotency",
        ),
    )
    # 按 (run_id, kind, slug) 也建索引 —— execute 阶段需要快速按 run 过滤
    op.create_index(
        "ix_migration_manifest_run_status",
        "migration_manifest_items",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_migration_manifest_run_status", table_name="migration_manifest_items")
    op.drop_table("migration_manifest_items")
    op.drop_table("migration_runs")
