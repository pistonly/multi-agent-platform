"""a764abf6 I1.(b) — archive metadata lint helper.

Surfaces consistency problems with the ``review.archived_at`` /
``review.archived_reason`` metadata so drift between the auto-archive
cascade (I1(b)) and the default filter (I1(c)) becomes observable.

Two checks per plan (b):

* **检测 1 — archived review 一致性**: archived reviews must NOT appear
  in the default ``list_reviews(include_archived=False)`` output.

  - ``N=2_transition`` phase (``include_archived`` 默认 True 过渡期,
    before release checklist flip): leaked archived review is
    ``WARN`` only (existing prod tests rely on the default and we do
    not want to fail mid-flight).
  - ``N=2_post_flip`` phase (``include_archived`` 默认 False,
    after release checklist flip): leaked archived review is
    ``FAIL`` (the new contract is violated).

* **检测 2 — plan_version drift**: every archived review's
  ``plan_version`` must be less than ``experiment.current_plan_version``.
  Drift (archived review referencing a plan that is still canonical)
  is always ``FAIL`` regardless of N=2 phase.

Output: list of :class:`ArchiveLintIssue` with ``code``,
``severity`` (``WARN`` / ``FAIL``), ``message``, and references to the
``review_id`` / ``plan_version`` involved.

The CLI / API wrapper passes ``n2_phase`` explicitly based on the
release checklist; this helper does not infer it from live prod data
(``list_reviews(include_archived=...)`` defaults are not introspectable
from the service layer without a deliberate API probe).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Review
from server.services.project_service import get_experiment
from server.services.review_service import list_reviews  # re-export for test monkey-patch

ArchiveLintCode = Literal[
    "ARCHIVED_IN_DEFAULT_LIST",
    "ARCHIVED_PLAN_VERSION_DRIFT",
]

ArchiveLintSeverity = Literal["WARN", "FAIL"]

ArchiveLintN2Phase = Literal["transition", "post_flip"]


@dataclass(frozen=True)
class ArchiveLintIssue:
    code: ArchiveLintCode
    severity: ArchiveLintSeverity
    message: str
    review_id: uuid.UUID | None = None
    plan_version: int | None = None


@dataclass(frozen=True)
class ArchiveLintResult:
    issues: list[ArchiveLintIssue] = field(default_factory=list)
    n2_phase: ArchiveLintN2Phase = "post_flip"
    reviews_total: int = 0
    reviews_archived: int = 0

    @property
    def has_failures(self) -> bool:
        return any(issue.severity == "FAIL" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "WARN" for issue in self.issues)


def archive_lint(
    db: Session,
    experiment_id: uuid.UUID,
    *,
    n2_phase: ArchiveLintN2Phase = "post_flip",
) -> ArchiveLintResult:
    """Lint helper — returns all ``ArchiveLintIssue``s found.

    Implementation steps:

    1. Pull all reviews (archived + non-archived).
    2. Pull ``list_reviews(include_archived=False)`` to model the default
       filter exactly as the API exposes it.
    3. 检测 1 — diff the two sets: any review that is archived but
       appears in the default list is an inconsistency. Severity depends
       on the supplied ``n2_phase``.
    4. 检测 2 — for every archived review, assert
       ``review.plan_version < experiment.current_plan_version``.
       Drift ⇒ ``FAIL``.
    """
    experiment = get_experiment(db, experiment_id)
    all_reviews = list(
        db.scalars(select(Review).where(Review.experiment_id == experiment_id))
    )
    default_list = list_reviews(db, experiment_id, include_archived=False)
    default_ids = {r.id for r in default_list}

    issues: list[ArchiveLintIssue] = []
    archived = [r for r in all_reviews if r.archived_at is not None]

    # 检测 1: archived review leaked into default list.
    severity_for_drift: ArchiveLintSeverity = (
        "WARN" if n2_phase == "transition" else "FAIL"
    )
    for review in archived:
        if review.id in default_ids:
            issues.append(
                ArchiveLintIssue(
                    code="ARCHIVED_IN_DEFAULT_LIST",
                    severity=severity_for_drift,
                    message=(
                        f"Review {review.id} is archived but appears in "
                        f"list_reviews(include_archived=False) default "
                        f"output (N=2 phase={n2_phase})"
                    ),
                    review_id=review.id,
                    plan_version=review.plan_version,
                )
            )

    # 检测 2: archived review's plan_version must be < current_plan_version.
    for review in archived:
        if review.plan_version >= experiment.current_plan_version:
            issues.append(
                ArchiveLintIssue(
                    code="ARCHIVED_PLAN_VERSION_DRIFT",
                    severity="FAIL",
                    message=(
                        f"Archived review {review.id} references "
                        f"plan_version={review.plan_version} but "
                        f"experiment.current_plan_version="
                        f"{experiment.current_plan_version}"
                    ),
                    review_id=review.id,
                    plan_version=review.plan_version,
                )
            )

    return ArchiveLintResult(
        issues=issues,
        n2_phase=n2_phase,
        reviews_total=len(all_reviews),
        reviews_archived=len(archived),
    )
