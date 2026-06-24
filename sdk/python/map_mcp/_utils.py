from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel


def dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        return {key: dump(item) for key, item in value.items()}
    return value


def parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError(f"Invalid UUID for {field}: {value}") from exc


def dumps_json(value: Any) -> str:
    return json.dumps(dump(value), ensure_ascii=False, indent=2)
