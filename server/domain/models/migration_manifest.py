"""DB → FS projection 存量迁移 manifest（实验 M2 I5：A5）。

两张表：

- ``migration_runs`` —— 一次 manifest 跑 = 一行（4 phase 推进留 audit 链）
- ``migration_manifest_items`` —— 每个待迁移 DB 实体（topic / experiment）
  = 一行；idempotency_key = SHA-256(project_id|kind|slug|content_hash)

幂等与中断恢复设计：

- ``idempotency_key`` 列加 UNIQUE constraint —— 同 (project_id, kind,
  slug, content_hash) 重复写会被 DB 层拦截，业务层走 INSERT OR IGNORE
  模式（先 INSERT 失败再看是否已存在），绝不会产生重复 item 行。
- ``status`` 状态机：``pending → in_flight → applied | failed | skipped``
  - 进程在 ``in_flight`` 中挂掉 → ``last_attempt_at`` 超过 STALE_AFTER
    时间（默认 30 分钟）就被下次 scan 重置为 ``pending``，重新走
    apply 流程（CAS 语义保证幂等，server 端不会写两份）。
- ``attempts`` + ``last_error`` —— retry budget 与错误归因；attempt 超过
  上限（默认 3）直接 ``failed`` 不再重试，留 audit trail 让 host
  手动介入。

与 fs_projection CAS 端点的关系：

- manifest 是 client-side 簿记 —— 真正的去重保证来自 server 端
  ``/fs/projection/delta`` 的 base_revision CAS + ``expected_hash``
  tombstone（I3 落地）；manifest 只是把「哪些 item 已经 apply 成功」
  缓存在 DB，避免每次重跑都重头扫。
- ``content_hash`` 与 FS 端 ``canonical_experiment_dict`` /
  ``canonical_topic_dict`` 口径必须一致 —— manifest compute 时调同一
  helper（见 ``server/services/migration_manifest_service._compute_content_hash``）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import Base


class MigrationRun(Base):
    __tablename__ = "migration_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[MigrationManifestItem]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class MigrationManifestItem(Base):
    __tablename__ = "migration_manifest_items"
    __table_args__ = (
        # 防重复：同 (project_id, kind, slug, content_hash) 只能有一行
        # —— 重复跑 scan 不会产生幽灵 item。
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("migration_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    slug: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run = relationship("MigrationRun", back_populates="items")
