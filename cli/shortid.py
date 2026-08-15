"""M54B: short-id (UUID prefix) resolution — generic ``ref + matcher`` helper.

E2: experiment-level ``--id`` values were typed ``uuid.UUID``, so an 8-hex
prefix never even reached the API — typer rejected it at parse time, and
agents were forced to copy full 36-char UUIDs out of table output
(docs/prd/v0.12.md M54B).

``resolve_ref`` turns a raw ``--id`` string into the canonical UUID:

* full UUID (36-char form or 32 hex digits) → passed through as-is;
* short prefix (8..31 hex digits) → the caller-supplied ``matcher`` runs a
  **DB-layer** prefix query (server: ``CAST(id AS CHAR) LIKE '<prefix>%'``,
  see ``svc.list_experiments(id_prefix=...)``) and:

  - exactly one hit → resolved to the full UUID;
  - no hit → usage error naming the prefix;
  - multiple hits → usage error listing candidate short forms + labels so
    the caller can lengthen the prefix;

* anything else → usage error explaining the accepted shapes.

Callers without DB access should fall back to a bounded in-memory scan
(one page, ``page_size`` capped) and say so — never pull the full table
(plan v2, review item r1).

Generic on purpose: the same helper serves future topic / agent short-id
work without carrying experiment-specific imports.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable

import typer

#: ``matcher(prefix) -> [(uuid, label), ...]`` — one bounded DB query.
Matcher = Callable[[str], list[tuple[uuid.UUID, str]]]

_HEX = re.compile(r"^[0-9a-f]+$")
_MAX_CANDIDATES_SHOWN = 5


def _short(cid: uuid.UUID) -> str:
    return str(cid).replace("-", "")[:12]


def normalize_uuid_like(raw: str | uuid.UUID | None) -> uuid.UUID | None:
    """Best-effort offline UUID normalization (no matcher / API call).

    Returns the UUID when ``raw`` is already a ``uuid.UUID`` or a full
    UUID string (36-char or 32-hex); returns ``None`` for short prefixes
    and anything unrecognized, so UUID-typed downstream calls (e.g.
    ``cli.main._run``'s escalation lookup) can be skipped instead of
    leaking a raw prefix string into an API path (M54B).
    """
    if isinstance(raw, uuid.UUID):
        return raw
    if not raw:
        return None
    text = raw.strip().lower().replace("-", "")
    if len(text) == 32 and _HEX.fullmatch(text):
        return uuid.UUID(text)
    return None


def resolve_ref(
    raw: str | uuid.UUID,
    *,
    kind: str,
    matcher: Matcher,
    label: str = "--id",
) -> uuid.UUID:
    """Resolve ``raw`` to a canonical UUID (full value or >=8-hex prefix).

    Raises ``typer.BadParameter`` (usage error, exit 2) with actionable
    messages for: missing value, malformed input, unknown prefix, or an
    ambiguous prefix (candidates listed).
    """
    if isinstance(raw, uuid.UUID):
        return raw
    if raw is None:
        raise typer.BadParameter(f"{label} is required")
    text = raw.strip().lower().replace("-", "")
    if not text:
        raise typer.BadParameter(f"{label} is required")

    if len(text) == 32 and _HEX.fullmatch(text):
        return uuid.UUID(text)
    if 8 <= len(text) < 32 and _HEX.fullmatch(text):
        candidates = matcher(text)
        if not candidates:
            raise typer.BadParameter(
                f"no {kind} matches id prefix '{text}'; "
                f"check the id or list {kind}s first"
            )
        if len(candidates) == 1:
            return candidates[0][0]
        shown = "\n".join(
            f"  {_short(cid)}  {name}" for cid, name in candidates[:_MAX_CANDIDATES_SHOWN]
        )
        more = "" if len(candidates) <= _MAX_CANDIDATES_SHOWN else "\n  ..."
        raise typer.BadParameter(
            f"id prefix '{text}' is ambiguous ({len(candidates)} matches); "
            f"lengthen the prefix. Candidates:\n{shown}{more}"
        )
    raise typer.BadParameter(
        f"invalid {kind} id {raw!r}: expected a full UUID or an 8..31 hex-digit "
        f"prefix (e.g. {_short(uuid.uuid4())})"
    )
