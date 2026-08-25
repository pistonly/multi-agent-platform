"""Agent token sha256 fast-lookup column.

T01（2026-08 认证优化）：``agents.api_token_sha256`` 存 sha256(token) 十六进制
（唯一索引），把每请求认证从 bcrypt 慢哈希（~200ms）退化为等值索引查找。
列为 NULL 的 legacy 行由 ``get_agent_by_token`` 在首次成功 bcrypt 校验后惰性
回填；``create_agent`` / ``bootstrap`` / ``reissue_agent_token`` 新签发的
token 同时写入 bcrypt 哈希与 sha256。

Revision ID: 051
Revises: 050
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    columns = {c["name"] for c in inspector.get_columns("agents")}
    with op.batch_alter_table("agents") as batch:
        if "api_token_sha256" not in columns:
            batch.add_column(sa.Column("api_token_sha256", sa.String(64), nullable=True))
    # unique=True 在 SQLite batch 模式下由显式索引保证（NULL 可重复）。
    index_names = {idx["name"] for idx in inspector.get_indexes("agents")}
    if "ix_agents_api_token_sha256" not in index_names:
        op.create_index(
            "ix_agents_api_token_sha256",
            "agents",
            ["api_token_sha256"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    index_names = {idx["name"] for idx in inspector.get_indexes("agents")}
    if "ix_agents_api_token_sha256" in index_names:
        op.drop_index("ix_agents_api_token_sha256", table_name="agents")
    columns = {c["name"] for c in inspector.get_columns("agents")}
    with op.batch_alter_table("agents") as batch:
        if "api_token_sha256" in columns:
            batch.drop_column("api_token_sha256")
