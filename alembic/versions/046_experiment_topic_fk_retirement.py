"""MAP slimming (v0.13 M58): retire ``experiments.topic_id`` FK.

``experiments.topic_id`` may now reference either a DB ``topics`` row or
an FS-plane topic id (uuid5 derived from the ``map/`` folder, no DB row) —
the M56 three-state topic routing extended to experiment creation
(``project_service.create_experiment`` falls back to
``fs_source_service.find_fs_topic_by_id`` when the id misses the DB).
A hard FK would reject those uuid5 values, so the constraint is retired;
topic existence / ownership gates move to the service layer.

- PostgreSQL: named constraint ``fk_experiments_topic_id`` (created in 006).
- SQLite: same named constraint, dropped via ``batch_alter_table`` — the
  alembic env does not enable ``PRAGMA foreign_keys`` for migrations, so
  the table rebuild is safe (same pattern as 042).
- The plain index ``ix_experiments_topic_id`` is kept (the model still
  declares ``index=True``).

Idempotent: re-running upgrade is a no-op once the FK is gone.
Downgrade re-creates the FK and will fail while any experiment still
references an FS-plane topic — clean up those rows first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "046"
down_revision: str | None = "045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_experiments_topic_id"


def _fk_exists(bind: object) -> bool:
    inspector = sa.inspect(bind)  # type: ignore[arg-type]
    if "experiments" not in inspector.get_table_names():
        return False
    return any(
        fk.get("name") == _FK_NAME for fk in inspector.get_foreign_keys("experiments")
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _fk_exists(bind):
        return  # idempotent guard — re-running upgrade must be a no-op

    if bind.dialect.name == "postgresql":
        op.drop_constraint(_FK_NAME, "experiments", type_="foreignkey")
    else:
        # SQLite cannot ALTER DROP CONSTRAINT outside of a table rebuild.
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.drop_constraint(_FK_NAME, type_="foreignkey")


def downgrade() -> None:
    bind = op.get_bind()
    if _fk_exists(bind):
        return  # idempotent guard

    if bind.dialect.name == "postgresql":
        op.create_foreign_key(_FK_NAME, "experiments", "topics", ["topic_id"], ["id"])
    else:
        with op.batch_alter_table("experiments") as batch_op:
            batch_op.create_foreign_key(_FK_NAME, "topics", ["topic_id"], ["id"])
