"""purge_audit_pollution — clean cross_persona_call audit rows that pre-date the capability gate.

authz experiment (0e6926fa) PR2: before this PR, the
``POST /experiments/{id}/cross-persona-call`` endpoint only enforced
``ensure_experiment_access`` (project membership), which let any
project member — including ``participant`` / ``reviewer`` personas —
write audit rows. Those rows are now ``rejected=True`` + 403, but the
historical rows pre-date the gate and need to be removed from the
audit log so R6 metrics stop double-counting them.

Usage::

    # dry-run (default): count rows that would be deleted
    python -m server.scripts.purge_audit_pollution

    # actually delete
    python -m server.scripts.purge_audit_pollution --apply

    # scope to one project
    python -m server.scripts.purge_audit_pollution --project-id <uuid> --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select

from server.db.session import SessionLocal
from server.domain.models import Agent, AgentRole, AuditLog
from server.services.audit_service import CROSS_PERSONA_CALL


def _whitelist_agent_ids(db) -> set[UUID]:
    """Agents allowed to call cross_persona_call: admin (any project) +
    host persona (same project as the audit row)."""
    admin_ids = set(
        db.scalars(select(Agent.id).where(Agent.role == AgentRole.admin)).all()
    )
    host_ids = set(
        db.scalars(
            select(Agent.id).where(Agent.name == "multi-agent-platform-host")
        ).all()
    )
    return admin_ids | host_ids


def _polluted_row_ids(db, project_id: UUID | None) -> tuple[list[UUID], int]:
    """Return (ids_to_purge, total_cross_persona_rows) for context."""
    whitelist = _whitelist_agent_ids(db)
    stmt = select(AuditLog.id, AuditLog.agent_id).where(
        AuditLog.action == CROSS_PERSONA_CALL
    )
    if project_id is not None:
        stmt = stmt.where(AuditLog.project_id == project_id)
    polluted: list[UUID] = []
    for row_id, agent_id in db.execute(stmt).all():
        if agent_id not in whitelist:
            polluted.append(row_id)
    total = len(polluted)
    return polluted, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete rows. Default is dry-run (count only).",
    )
    parser.add_argument(
        "--project-id",
        type=UUID,
        default=None,
        help="Scope the purge to one project.",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        polluted, total = _polluted_row_ids(db, args.project_id)
        ts = datetime.utcnow().isoformat(timespec="seconds")
        if not args.apply:
            print(
                f"[dry-run {ts}] would delete {len(polluted)} polluted "
                f"cross_persona_call audit rows (project_id={args.project_id or 'ALL'})"
            )
            return 0
        if not polluted:
            print(f"[apply {ts}] no polluted rows found")
            return 0
        # Delete in chunks to avoid huge single statements; SQLite
        # parameter cap is 999, Postgres has none but the chunk keeps
        # the transaction small.
        CHUNK = 200
        deleted = 0
        for i in range(0, len(polluted), CHUNK):
            chunk = polluted[i : i + CHUNK]
            stmt = delete(AuditLog).where(AuditLog.id.in_(chunk))
            result = db.execute(stmt)
            deleted += result.rowcount or 0
        db.commit()
        print(
            f"[apply {ts}] deleted {deleted} polluted cross_persona_call "
            f"audit rows (project_id={args.project_id or 'ALL'})"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
