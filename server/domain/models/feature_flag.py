"""Project-level feature flags (实验 M2 I4：A4).

Single table, project-scoped, generic key/value store. The only registered
key today is ``fs_stop_duplicate_insert``; new flags just land as rows
without schema change. Generic-by-design but consumed narrowly by
``feature_flag_service.fs_stop_duplicate_insert`` (the gate that reads it).

设计要点：

- **Key 短定长**（String(64)）—— flag_key 走代码常量而不是 free-form
  string，避免 caller 拼写错「fs_stop_duplicate_insert」变成无人读的
  「fs_stop_dup_insert」。新 flag 加新常量 + 注册到 ``feature_flag_service``
  的 known flags 表。
- **Value 短定长**（String(32)）—— phase/key 字符串（``off`` / ``phase1``
  / ``on``），人类可读；不是 JSON，避免 schema 演化。
- **NOT NULL set_by_agent_id** —— 每个 flip 都有 actor 归属；连 bootstrap
  flip 也必须显式携带 host/admin agent。
- **reason free text** —— kill switch / fail closed flip 必须写 reason；
  CLI 强制要求非空 reason，便于事后审计「为什么切回旧写路径」。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import Base


class ProjectFeatureFlag(Base):
    __tablename__ = "project_feature_flags"
    __table_args__ = (
        Index(
            "ix_project_feature_flags_project_id",
            "project_id",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), primary_key=True
    )
    flag_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    flag_value: Mapped[str] = mapped_column(String(32), nullable=False)
    set_by_agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id"), nullable=False
    )
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    setter = relationship("Agent", foreign_keys=[set_by_agent_id])
