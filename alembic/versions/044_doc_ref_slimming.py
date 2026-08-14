"""MAP slimming: local MD file references for topics and experiments.

Add columns so the platform stores file paths + excerpts instead of
full content bodies:

- ``topics.slug`` — human-readable identifier for file path convention
  (e.g. ``docs/topics/<slug>/round1-host.md``). Nullable for backward
  compat; UNIQUE constraint allows multiple NULLs (SQLite/PostgreSQL).
- ``topic_comments.file_path`` — relative path to the local MD file
  containing the comment content. Nullable; when present, ``body`` may
  be a stub.
- ``topic_comments.excerpt`` — short summary (<200 chars) for list
  views and notifications. Nullable.
- ``experiments.plan_file_path`` — relative path to the plan MD file.
  Nullable; when present, ``PlanVersion.content_md`` stores a stub.
- ``experiments.log_file_path`` — relative path to the completion log
  MD file. Nullable; when present, ``ExperimentLog.content_md`` stores
  a stub.

Idempotent: re-running upgrade is a no-op if columns already exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "044"
down_revision: str | None = "043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- topics.slug ---
    if "topics" in inspector.get_table_names():
        if not _has_column(inspector, "topics", "slug"):
            op.add_column(
                "topics",
                sa.Column("slug", sa.String(length=256), nullable=True),
            )
        if not _has_index(inspector, "topics", "uq_topics_slug"):
            op.create_index(
                "uq_topics_slug",
                "topics",
                ["slug"],
                unique=True,
                sqlite_where=sa.text("slug IS NOT NULL"),
                postgresql_where=sa.text("slug IS NOT NULL"),
            )

    # --- topic_comments.file_path + excerpt ---
    if "topic_comments" in inspector.get_table_names():
        if not _has_column(inspector, "topic_comments", "file_path"):
            op.add_column(
                "topic_comments",
                sa.Column("file_path", sa.Text(), nullable=True),
            )
        if not _has_column(inspector, "topic_comments", "excerpt"):
            op.add_column(
                "topic_comments",
                sa.Column("excerpt", sa.String(length=200), nullable=True),
            )

    # --- experiments.plan_file_path + log_file_path ---
    if "experiments" in inspector.get_table_names():
        if not _has_column(inspector, "experiments", "plan_file_path"):
            op.add_column(
                "experiments",
                sa.Column("plan_file_path", sa.Text(), nullable=True),
            )
        if not _has_column(inspector, "experiments", "log_file_path"):
            op.add_column(
                "experiments",
                sa.Column("log_file_path", sa.Text(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "experiments" in inspector.get_table_names():
        if _has_column(inspector, "experiments", "log_file_path"):
            op.drop_column("experiments", "log_file_path")
        if _has_column(inspector, "experiments", "plan_file_path"):
            op.drop_column("experiments", "plan_file_path")

    if "topic_comments" in inspector.get_table_names():
        if _has_column(inspector, "topic_comments", "excerpt"):
            op.drop_column("topic_comments", "excerpt")
        if _has_column(inspector, "topic_comments", "file_path"):
            op.drop_column("topic_comments", "file_path")

    if "topics" in inspector.get_table_names():
        if _has_index(inspector, "topics", "uq_topics_slug"):
            op.drop_index("uq_topics_slug", table_name="topics")
        if _has_column(inspector, "topics", "slug"):
            op.drop_column("topics", "slug")
