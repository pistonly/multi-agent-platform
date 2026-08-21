"""Shared content-origin metadata for FS / Git / projection reads."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ContentSourceMeta(BaseModel):
    """Unified origin metadata for content-derived remote reads.

    ``stale`` is only set from verifiable server-side signals (freshness SLA,
    missing projection). The server never guesses whether the client still
    has unpushed local files.
    """

    content_source: str
    source_revision: str | None = None
    source_content_hash: str | None = None
    source_updated_at: datetime | None = None
    stale: bool = False
    stale_reason: str | None = None
