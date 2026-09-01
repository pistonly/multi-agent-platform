import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class FsProjection(Base):
    """FS plane 投影缓存（远程/容器部署的读侧回退源）。

    内容主权仍在本地 ``map/`` 文件夹；本表只存 ``map fs push`` 上行的
    解析快照（FsProjectionPushRequest 的 JSON 形态），每 project 一行、
    幂等覆盖。server 读路径在 workspace 不可达时回退到该快照；commit
    验证型写时顺带把 fields 应用到快照，避免额外一轮 push。
    """

    __tablename__ = "fs_projections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, unique=True, index=True
    )
    pushed_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    publisher_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    client_workspace: Mapped[str] = mapped_column(String(1024), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pushed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FsWriteReceipt(Base):
    """一次性 FS 写凭证的消费记录，阻止 commit token 重放。"""

    __tablename__ = "fs_write_receipts"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
