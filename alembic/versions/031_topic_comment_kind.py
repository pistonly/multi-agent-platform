"""topic_comments.kind (user|system)

Revision ID: 031
Revises: 030
Create Date: 2026-07-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "topic_comments" not in inspector.get_table_names():
        return
    if _has_column(inspector, "topic_comments", "kind"):
        return

    if bind.dialect.name == "postgresql":
        op.execute("CREATE TYPE topiccommentkind AS ENUM ('user', 'system')")
        op.add_column(
            "topic_comments",
            sa.Column(
                "kind",
                sa.Enum("user", "system", name="topiccommentkind"),
                nullable=False,
                server_default="user",
            ),
        )
    else:
        op.add_column(
            "topic_comments",
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="user"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "topic_comments" not in inspector.get_table_names():
        return
    if not _has_column(inspector, "topic_comments", "kind"):
        return
    op.drop_column("topic_comments", "kind")
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS topiccommentkind")
