"""topic comment_seq + topic_read_cursors (T3 D1/D2)

Revision ID: 025
Revises: 024
Create Date: 2026-07-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "topic_comments" in tables and not _has_column(inspector, "topic_comments", "comment_seq"):
        op.add_column(
            "topic_comments",
            sa.Column("comment_seq", sa.Integer(), nullable=True),
        )
        if bind.dialect.name == "postgresql":
            op.execute(
                """
                UPDATE topic_comments AS tc
                SET comment_seq = sub.rn
                FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY topic_id ORDER BY created_at, id
                           ) AS rn
                    FROM topic_comments
                ) AS sub
                WHERE tc.id = sub.id
                """
            )
        else:
            op.execute(
                """
                UPDATE topic_comments
                SET comment_seq = (
                    SELECT COUNT(*) FROM topic_comments AS t2
                    WHERE t2.topic_id = topic_comments.topic_id
                      AND (t2.created_at < topic_comments.created_at
                           OR (t2.created_at = topic_comments.created_at AND t2.id <= topic_comments.id))
                )
                """
            )
        # sqlite 不支持 ``ALTER COLUMN ... SET NOT NULL``，用 batch mode
        # 重建表（alembic 标准 sqlite DDL 处理方式）。PG 直接 alter_column。
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("topic_comments") as batch_op:
                batch_op.alter_column("comment_seq", nullable=False)
        else:
            op.alter_column("topic_comments", "comment_seq", nullable=False)

    if "topic_read_cursors" not in tables:
        op.create_table(
            "topic_read_cursors",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("topic_id", sa.Uuid(), nullable=False),
            sa.Column("agent_id", sa.Uuid(), nullable=False),
            sa.Column("last_read_comment_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["topic_id"], ["topics.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("topic_id", "agent_id", name="uq_topic_read_cursor_topic_agent"),
        )
        op.create_index(
            op.f("ix_topic_read_cursors_topic_id"),
            "topic_read_cursors",
            ["topic_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_topic_read_cursors_agent_id"),
            "topic_read_cursors",
            ["agent_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "topic_read_cursors" in tables:
        op.drop_index(op.f("ix_topic_read_cursors_agent_id"), table_name="topic_read_cursors")
        op.drop_index(op.f("ix_topic_read_cursors_topic_id"), table_name="topic_read_cursors")
        op.drop_table("topic_read_cursors")

    if "topic_comments" in tables and _has_column(inspector, "topic_comments", "comment_seq"):
        op.drop_column("topic_comments", "comment_seq")
