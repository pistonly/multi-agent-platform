#!/usr/bin/env python3
"""SSE event schema overlap check (acceptance C)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.domain.models import Comment, Experiment, Mention, Notification, Topic
from server.services.sse_event_schemas import (
    NOTIFICATION_CREATED_SSE_FIELDS,
    SSE_TRANSPORT_ONLY_FIELDS,
)


def _model_field_names(model) -> set[str]:
    return {column.key for column in model.__table__.columns}


def _service_union_fields() -> set[str]:
    fields = set()
    for model in (Topic, Experiment, Comment, Mention, Notification):
        fields |= _model_field_names(model)
    # SSE aliases
    fields |= {"notification_id", "event", "type"}
    return fields


def sse_fields_not_in_service_union() -> list[str]:
    union = _service_union_fields()
    extras = []
    for field in NOTIFICATION_CREATED_SSE_FIELDS:
        if field in SSE_TRANSPORT_ONLY_FIELDS:
            continue
        if field not in union:
            extras.append(field)
    return extras


def main() -> int:
    extras = sse_fields_not_in_service_union()
    if extras:
        print("SSE fields not covered by service model union:", file=sys.stderr)
        for field in extras:
            print(f"  - {field}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
