"""MAP slimming (v0.13 M57): per-row file reference for experiment logs.

``experiment_logs.file_path`` — relative path to the local MD file
containing the log content. Nullable; when present, ``content_md``
stores a stub (``See file: <path>``). This mirrors ``topic_comments.
file_path`` (044) but at the row level because logs are append-only
multi-row entries, unlike the single-value ``experiments.log_file_path``
which holds the completion log only.

Idempotent: re-running upgrade is a no-op if the column already exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "045"
down_revision: str | None = "044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if (
        "experiment_logs" in inspector.get_table_names()
        and not _has_column(inspector, "experiment_logs", "file_path")
    ):
        op.add_column(
            "experiment_logs",
            sa.Column("file_path", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if (
        "experiment_logs" in inspector.get_table_names()
        and _has_column(inspector, "experiment_logs", "file_path")
    ):
        op.drop_column("experiment_logs", "file_path")
