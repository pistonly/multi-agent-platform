from dataclasses import dataclass, field
from typing import Any, Literal

from map_client.plan_evidence import (
    PlanEvidenceKeys,
    PlanFrontmatterParseError,
    parse_plan_evidence_keys,
)

# arch experiment (0519e2a3) PR2: ``EVIDENCE_METADATA_KEYS`` and
# ``metadata_has_completion_evidence`` moved to ``map_sdk.evidence``.
# Keep them re-exported from here so existing server-side imports
# keep working without churning every call site in this PR. CLI
# switched to ``from map_sdk.evidence import ...`` already.
from map_sdk.evidence import (  # noqa: E402, F401
    EVIDENCE_METADATA_KEYS,
    metadata_has_completion_evidence,
)

# --- 8ac93d4e I1.b — plan evidence_keys soft validation ---------------------


WarningCode = Literal["MISSING_EVIDENCE_KEY"]


@dataclass(frozen=True)
class EvidenceWarning:
    code: WarningCode
    missing_key: str
    plan_required: bool = True
    log_provided: bool = False


@dataclass(frozen=True)
class EvidenceValidationResult:
    warnings: list[EvidenceWarning] = field(default_factory=list)
    parse_error: str | None = None
    plan_keys: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Soft validation: always True. Host is expected to follow up
        with another log if warnings indicate missing evidence."""
        return True


def validate_log_evidence(
    *,
    plan_md: str | None,
    metadata: dict[str, Any] | None,
) -> EvidenceValidationResult:
    """Compare ``metadata`` keys against plan ``evidence_keys``.

    Cases:
    * ``plan_md`` is None or empty → empty result (no plan to validate against).
    * Plan has frontmatter but YAML parse fails → ``parse_error`` set,
      no warnings (caller surfaces stderr warn).
    * Plan frontmatter OK + has ``evidence_keys`` → return
      ``MISSING_EVIDENCE_KEY`` warning for each plan key absent from
      ``metadata``.
    * Plan frontmatter OK but no ``evidence_keys`` key → empty result.
    """
    if not plan_md:
        return EvidenceValidationResult()

    try:
        parsed: PlanEvidenceKeys = parse_plan_evidence_keys(plan_md)
    except PlanFrontmatterParseError as exc:
        return EvidenceValidationResult(parse_error=str(exc))

    if not parsed.keys:
        return EvidenceValidationResult(plan_keys=parsed.keys)

    provided = set(metadata.keys()) if isinstance(metadata, dict) else set()
    warnings: list[EvidenceWarning] = []
    for key in parsed.keys:
        if key not in provided:
            warnings.append(
                EvidenceWarning(
                    code="MISSING_EVIDENCE_KEY",
                    missing_key=key,
                    plan_required=True,
                    log_provided=False,
                )
            )
    return EvidenceValidationResult(warnings=warnings, plan_keys=parsed.keys)


# --- 50cddb7e I4 (A3) — complete 时 pytest_summary 机器校验 ---------------------


@dataclass(frozen=True)
class PytestSummaryValidation:
    """complete 时对 ``metadata["pytest_summary"]`` 的机器校验结果。

    * ``reject_reason`` — ``failed > 0`` 且未提供 ``--known-failures``：
      complete 必须拒绝（state 报错给 host 可落地的修复提示）。
    * ``exempted`` — ``failed > 0`` 但 host 用 ``--known-failures`` 显式
      豁免（诚实记录而非死板门禁）。
    * ``warnings`` — 软信号（total 与 CI 基线不符、pytest_summary 非结构化），
      永不阻断 complete。
    """

    reject_reason: str | None = None
    exempted: bool = False
    exempted_known_failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _as_nonneg_int(value: object) -> int | None:
    """Return a non-negative int when ``value`` is int-like, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def validate_pytest_summary(
    pytest_summary: object,
    *,
    known_failures: list[str] | None = None,
    ci_baseline_total: int | None = None,
) -> PytestSummaryValidation:
    """Machine-check a complete-time ``pytest_summary`` entry.

    结构化形态（CLI 文档形态）：``{"total": N, "passed": N, "failed": N}``。

    * 非 dict（如历史字符串 ``"unit passed"``）→ 无法机器核对 failed，
      仅 warning 提示改用结构化形态，不拒绝（test_m3_flow 等旧形态兼容）。
    * ``failed > 0`` 且无 ``known_failures`` → ``reject_reason``（硬门禁）。
    * ``failed > 0`` 且有 ``known_failures`` → 豁免放行 + 登记 ref。
    * ``total`` 与 ``ci_baseline_total``（已配置时）不符 → warning。
    """
    warnings: list[str] = []
    if not isinstance(pytest_summary, dict):
        return PytestSummaryValidation(
            warnings=(
                "pytest_summary 未用结构化形态 {total, passed, failed}，跳过 "
                "failed>0 机器校验——请改为结构化形态以获得 failed 门禁。",
            )
        )

    failed = _as_nonneg_int(pytest_summary.get("failed"))
    exempt = [ref for ref in (known_failures or []) if ref]
    total = _as_nonneg_int(pytest_summary.get("total"))

    if failed is not None and failed > 0:
        if exempt:
            return PytestSummaryValidation(
                exempted=True,
                exempted_known_failures=tuple(exempt),
                warnings=(
                    f"pytest_summary.failed={failed} > 0，已以 --known-failures "
                    + " ".join(exempt)
                    + " 显式豁免（诚实记录放行）。",
                ),
            )
        return PytestSummaryValidation(
            reject_reason=(
                f"pytest_summary.failed={failed} > 0：测试未全绿，complete 被拒。"
                "修复失败用例后重跑 pytest 并更新 --metadata 的 pytest_summary；"
                "如为已登记债条目请用 --known-failures <ref> 显式豁免。"
            )
        )

    if total is not None and ci_baseline_total is not None and total != ci_baseline_total:
        warnings.append(
            f"pytest_summary.total={total} 与 CI 基线 {ci_baseline_total} 不符"
            "（单机收集数与 CI 环境差异可忽略）。"
        )
    return PytestSummaryValidation(warnings=tuple(warnings))


