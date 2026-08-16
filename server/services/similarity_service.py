"""b72d0542 I1.b(2)(b) — Log content similarity soft validator.

Wraps the plan-(b) "embedding cosine + threshold 0.7" check with a
structured warning shape and the soft-validation invariant
(``valid`` is always True so ``experiment log`` is never blocked).

The real cosine implementation lands in plan-(f) (``sentence-transformers
/all-MiniLM-L6-v2``); for now this stub fires a deterministic
placeholder similarity so the (e) ``--force`` + audit plumbing can be
exercised end-to-end:

* identical body to the previous log on the same experiment →
  ``score=1.0`` → ``HIGH_CONTENT_SIMILARITY`` warning
* otherwise → ``score=0.0`` → no warning

Threshold, model id, and warning code are exported as constants so the
audit/CLI layers read them from a single source of truth. Once (f)
ships, ``_score_against_last_log`` is swapped to the real embedding
without touching callers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import ExperimentLog

SIMILARITY_THRESHOLD: float = 0.7
SIMILARITY_MODEL_ID: str = "placeholder:jaccard-v0"

SimilarityWarningCode = Literal["HIGH_CONTENT_SIMILARITY"]


@dataclass(frozen=True)
class SimilarityWarning:
    code: SimilarityWarningCode
    score: float
    threshold: float
    ref_log_id: uuid.UUID
    model: str


@dataclass(frozen=True)
class SimilarityValidationResult:
    warnings: list[SimilarityWarning] = field(default_factory=list)
    threshold: float = SIMILARITY_THRESHOLD
    model: str = SIMILARITY_MODEL_ID
    # v0.13 M57 slim form: set to ``"slim form"`` when the content-based
    # check was intentionally skipped (``ExperimentLogCreate.file_path``
    # form — stub-vs-stub / stub-vs-full comparisons are meaningless).
    # Callers surface this explicitly instead of silently treating the
    # empty warning list as "checked and clean".
    skipped_reason: str | None = None

    @property
    def valid(self) -> bool:
        """Soft validation invariant: always True (b72d0542 I1.b(2)).

        Mirrors :class:`evidence_service.EvidenceValidationResult.valid`.
        Host is expected to follow up with another log or pass
        ``force_skip_similarity=True`` to acknowledge the warning.
        """
        return True


def _score_against_last_log(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    content_md: str,
) -> tuple[float, uuid.UUID | None]:
    """Return ``(score, ref_log_id)`` against the most recent prior log.

    Placeholder similarity (b72d0542 I1.b(2) pre-(f)):
        score = 1.0 if previous log body is byte-identical, else 0.0
        ref_log_id = previous log id (always set when prior log exists)

    Real implementation in plan-(f) uses
    ``sentence-transformers/all-MiniLM-L6-v2`` cosine similarity.
    """
    stmt = (
        select(ExperimentLog)
        .where(ExperimentLog.experiment_id == experiment_id)
        .order_by(ExperimentLog.log_index.desc())
        .limit(1)
    )
    last = db.scalar(stmt)
    if last is None:
        return 0.0, None
    if last.content_md == content_md:
        return 1.0, last.id
    return 0.0, last.id


def validate_log_similarity(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    content_md: str,
) -> SimilarityValidationResult:
    """Compute similarity against the most-recent prior log.

    Soft validation — returns warnings but never raises. The log create
    path always succeeds; the warning is purely advisory.
    """
    score, ref_log_id = _score_against_last_log(
        db, experiment_id=experiment_id, content_md=content_md
    )
    warnings: list[SimilarityWarning] = []
    if ref_log_id is not None and score >= SIMILARITY_THRESHOLD:
        warnings.append(
            SimilarityWarning(
                code="HIGH_CONTENT_SIMILARITY",
                score=score,
                threshold=SIMILARITY_THRESHOLD,
                ref_log_id=ref_log_id,
                model=SIMILARITY_MODEL_ID,
            )
        )
    return SimilarityValidationResult(warnings=warnings)
