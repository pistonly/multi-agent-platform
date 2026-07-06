#!/usr/bin/env python3
"""Migrate open action items on closed/archived topics (experiment 68a8095e)."""

from __future__ import annotations

import argparse
import sys
import uuid

from server.db.session import SessionLocal
from server.services import action_item_migration_service as mig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing.")
    parser.add_argument(
        "--migrate-to",
        type=uuid.UUID,
        default=None,
        help="Opt-in: rebind open items to this topic id instead of cascade backlog.",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        results = mig.migrate_stale_open_action_items(
            db,
            migrate_to_topic_id=args.migrate_to,
            dry_run=args.dry_run,
        )
    finally:
        db.close()

    for result in results:
        prefix = "would " if result.dry_run else ""
        if result.skipped:
            print(f"skip {result.action_item_id}: {result.reason}")
            continue
        print(
            f"{prefix}{result.strategy} {result.action_item_id} "
            f"from={result.from_topic_id} to={result.to_topic_id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
