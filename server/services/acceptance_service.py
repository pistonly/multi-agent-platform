from __future__ import annotations

import hashlib
import re

from map_types.enums import AcceptanceType

from server.domain.schemas import AcceptanceStatusRead
from server.services.errors import StateTransitionError
from server.services.evidence_service import metadata_has_completion_evidence

_ACCEPTANCE_MARKER_RE = re.compile(
    r"\[acceptance_type:\s*(?P<kind>[a-zA-Z_]+)\]",
    re.IGNORECASE,
)
_ACCEPTANCE_ITEM_RE = re.compile(
    r"^\s*(?:[-*]|\d+[.)])\s*"
    r"\[acceptance_type:\s*(?P<kind>[a-zA-Z_]+)\]\s*"
    r"(?P<description>.+?)\s*$",
    re.IGNORECASE,
)


def parse_acceptance_status(
    content_md: str, *, completion_metadata: dict | None = None
) -> list[AcceptanceStatusRead]:
    statuses: list[AcceptanceStatusRead] = []
    has_generic_evidence = metadata_has_completion_evidence(completion_metadata)
    for line in content_md.splitlines():
        match = _ACCEPTANCE_ITEM_RE.match(line)
        if match is None:
            continue
        raw_kind = match.group("kind").lower()
        try:
            acceptance_type = AcceptanceType(raw_kind)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in AcceptanceType)
            raise StateTransitionError(
                f"Unknown acceptance_type {raw_kind!r}; allowed values: {allowed}"
            ) from exc
        description = " ".join(match.group("description").split())
        acceptance_id = _stable_acceptance_id(acceptance_type.value, description)
        statuses.append(
            AcceptanceStatusRead(
                id=acceptance_id,
                description=description,
                acceptance_type=acceptance_type,
                evidence_provided=has_generic_evidence
                or _metadata_has_acceptance_evidence(
                    completion_metadata,
                    acceptance_id=acceptance_id,
                    acceptance_type=acceptance_type.value,
                    description=description,
                ),
                reviewer_verdict=None,
            )
        )
    return statuses


def _stable_acceptance_id(acceptance_type: str, description: str) -> str:
    digest = hashlib.sha1(f"{acceptance_type}:{description}".encode()).hexdigest()
    return f"acc-{digest[:12]}"


def _metadata_has_acceptance_evidence(
    metadata: object, *, acceptance_id: str, acceptance_type: str, description: str
) -> bool:
    if not isinstance(metadata, dict):
        return False
    acceptance = metadata.get("acceptance")
    if isinstance(acceptance, dict):
        for key in (acceptance_id, acceptance_type, description):
            value = acceptance.get(key)
            if value not in (None, "", [], {}, False):
                return True
    if isinstance(acceptance, list):
        for item in acceptance:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence") or item.get("evidence_provided")
            if evidence in (None, "", [], {}, False):
                continue
            if (
                item.get("id") == acceptance_id
                or item.get("acceptance_type") == acceptance_type
                or item.get("description") == description
            ):
                return True
    return False
