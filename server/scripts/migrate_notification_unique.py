"""Notification UNIQUE(recipient_agent_id, group_key) migration (race PR2).

Independent script — does NOT go through alembic. The plan's rationale:
``CREATE UNIQUE INDEX CONCURRENTLY`` cannot run inside a transaction, and
alembic migrations are always wrapped in a transaction. This script
therefore executes raw SQL against the live database.

Usage
-----
    # dry-run: print plan, no DDL
    python -m server.scripts.migrate_notification_unique --dry-run

    # actually create the index
    python -m server.scripts.migrate_notification_unique --execute

    # idempotent: re-run safely (detects existing index)

PG-only
-------
SQLite / MySQL are intentionally skipped because:

* SQLite test/dev: the model's ``UniqueConstraint`` already enforces it
  via ``create_all``; production SQLite is not in scope for race PR2.
* MySQL: race PR2 is PG-only (per the topic-level scope and per
  ``multi-waker 跨机器部署需 PG`` PRD note). MySQL would need a separate
  ``ALTER TABLE ... ADD UNIQUE`` script.

Pre-flight (data dedup)
-----------------------
The unique constraint assumes no duplicate ``(recipient, group_key)``
pairs already exist. The plan's current production state should be
clean (the application layer's select-then-insert was the only path),
but a belt-and-braces dedup runs first: for each duplicate cluster,
keep the row with the highest ``wake_version`` (or ``updated_at`` as
tiebreaker) and delete the rest. The dedup log is printed so an
operator can sanity-check the count.

Failure handling
----------------
* ``CREATE UNIQUE INDEX CONCURRENTLY`` may leave an INVALID index if
  the second attempt fails or is interrupted. The script ends with a
  defensive ``DROP INDEX CONCURRENTLY IF EXISTS invalid_idx`` so the
  table is not left carrying a bloat index that the planner won't use.
* Re-runs are idempotent: existing-index detection skips the CREATE
  and reports current row counts.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger("map.scripts.migrate_notification_unique")

INDEX_NAME = "uq_notifications_recipient_group_key"


def _is_pg(conn: Connection) -> bool:
    return conn.dialect.name == "postgresql"


def _index_exists(conn: Connection, table: str, index: str) -> bool:
    rows = conn.exec_driver_sql(
        "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
        "AND tablename = %s AND indexname = %s",
        (table, index),
    ).first()
    return rows is not None


def _count_duplicates(conn: Connection) -> int:
    """Return the number of duplicate (recipient_agent_id, group_key)
    rows that the new unique index would reject. Zero is the target."""
    rows = conn.exec_driver_sql(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "  SELECT recipient_agent_id, group_key, COUNT(*) AS n "
            "  FROM notifications "
            "  WHERE group_key IS NOT NULL "
            "  GROUP BY recipient_agent_id, group_key HAVING COUNT(*) > 1"
            ") d"
        )
    ).first()
    return int(rows[0]) if rows else 0


def _deduplicate(conn: Connection) -> int:
    """Delete duplicate rows, keeping the most recently updated per
    (recipient, group_key). Returns the number of rows deleted.
    """
    # Use a CTE: pick the keeper row (highest wake_version, then
    # most-recent updated_at, then most-recent created_at), delete the
    # rest. ctid is PG-specific but this script is PG-only.
    sql = sa.text(
        """
        WITH ranked AS (
          SELECT
            ctid,
            ROW_NUMBER() OVER (
              PARTITION BY recipient_agent_id, group_key
              ORDER BY wake_version DESC, updated_at DESC, created_at DESC
            ) AS rn
          FROM notifications
          WHERE group_key IS NOT NULL
        ),
        deleted AS (
          DELETE FROM notifications n
          USING ranked r
          WHERE n.ctid = r.ctid AND r.rn > 1
          RETURNING n.id
        )
        SELECT COUNT(*) FROM deleted
        """
    )
    rows = conn.exec_driver_sql(sql).first()
    return int(rows[0]) if rows else 0


def _create_index(conn: Connection) -> None:
    # CONCURRENTLY cannot run in a transaction; the script is invoked
    # outside any alembic revision for that reason.
    conn.exec_driver_sql(
        sa.text(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
            f"ON notifications (recipient_agent_id, group_key) "
            f"WHERE group_key IS NOT NULL"
        )
    )


def _drop_invalid_index(conn: Connection) -> bool:
    """If a prior failed run left an INVALID index, drop it.

    Returns True if a stale invalid index was found and dropped.
    """
    rows = conn.exec_driver_sql(
        sa.text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = 'notifications' "
            "AND indexname LIKE %s"
        ),
        (f"%{INDEX_NAME}%",),
    ).all()
    names = [r[0] for r in rows]
    dropped_any = False
    for name in names:
        # pg_index.indisvalid is False for indexes whose build failed.
        invalid = conn.exec_driver_sql(
            sa.text("SELECT indisvalid FROM pg_index WHERE indexname = :n"),
            {"n": name},
        ).first()
        if invalid is not None and not invalid[0]:
            conn.exec_driver_sql(
                sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {sa.quoted_name(name, quote=True)}")
            )
            logger.warning("dropped invalid index notifications.%s", name)
            dropped_any = True
    return dropped_any


def run(engine: Engine, *, dry_run: bool, execute: bool) -> int:
    if not execute and not dry_run:
        print("Nothing to do: pass --dry-run or --execute", file=sys.stderr)
        return 2

    with engine.begin() as conn:
        if not _is_pg(conn):
            print(
                f"skip: dialect={conn.dialect.name!r} (PG-only script; "
                f"SQLite/MySQL use the model's UniqueConstraint via create_all)",
                file=sys.stderr,
            )
            return 0

        if _index_exists(conn, "notifications", INDEX_NAME):
            print(f"index notifications.{INDEX_NAME} already exists — nothing to do")
            return 0

        duplicates = _count_duplicates(conn)
        if duplicates:
            print(f"WARNING: {duplicates} duplicate (recipient, group_key) clusters exist")
            if dry_run:
                print("dry-run: would deduplicate then create index")
                return 1
            assert execute
            deleted = _deduplicate(conn)
            print(f"deduplicated {deleted} rows")
        elif dry_run:
            print("dry-run: no duplicates, would create index")
            return 0

        if execute:
            _create_index(conn)
            print(f"created index notifications.{INDEX_NAME}")
            _drop_invalid_index(conn)
            return 0
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print plan only")
    parser.add_argument("--execute", action="store_true", help="actually create the index")
    parser.add_argument(
        "--database-url",
        default=None,
        help="override MAP_DATABASE_URL (default: use the app's configured engine)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.database_url:
        engine = sa.create_engine(args.database_url)
    else:
        from server.config import get_settings
        from server.db.session import engine as app_engine

        get_settings()  # validates env
        engine = app_engine

    return run(engine, dry_run=args.dry_run, execute=args.execute)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
