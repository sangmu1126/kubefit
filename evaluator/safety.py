from typing import Literal

from pydantic import BaseModel

from recommender import ResourceRecommendation


class EligibilityCheck(BaseModel):
    code: Literal[
        "recommendation_readiness",
        "oom_risk",
        "cpu_throttling_risk",
    ]
    status: Literal["pass", "warning", "block"]
    reason: str


class PatchEligibility(BaseModel):
    status: Literal["eligible", "blocked"]
    checks: list[EligibilityCheck]
    blocking_reasons: list[str]
    warnings: list[str]


def evaluate_patch_eligibility(
    recommendation: ResourceRecommendation,
) -> PatchEligibility:
    checks = [_readiness_check(recommendation)]
    checks.extend(
        [
            _risk_check("oom_risk", "OOM", recommendation.risk.oom),
            _risk_check(
                "cpu_throttling_risk",
                "CPU throttling",
                recommendation.risk.cpu_throttling,
            ),
        ]
    )
    blocking_reasons = [check.reason for check in checks if check.status == "block"]
    warnings = [check.reason for check in checks if check.status == "warning"]
    return PatchEligibility(
        status="blocked" if blocking_reasons else "eligible",
        checks=checks,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
    )


def _readiness_check(recommendation: ResourceRecommendation) -> EligibilityCheck:
    if recommendation.readiness.status == "ready":
        return EligibilityCheck(
            code="recommendation_readiness",
            status="pass",
            reason="recommendation evidence satisfies readiness requirements",
        )
    detail = "; ".join(recommendation.readiness.reasons)
    return EligibilityCheck(
        code="recommendation_readiness",
        status="block",
        reason=f"recommendation evidence is insufficient: {detail}",
    )


def _risk_check(
    code: Literal["oom_risk", "cpu_throttling_risk"],
    label: str,
    risk: Literal["low", "medium", "high", "unknown"],
) -> EligibilityCheck:
    if risk == "low":
        return EligibilityCheck(
            code=code,
            status="pass",
            reason=f"{label} risk is low",
        )
    if risk == "medium":
        return EligibilityCheck(
            code=code,
            status="warning",
            reason=f"{label} risk is medium and requires reviewer attention",
        )
    return EligibilityCheck(
        code=code,
        status="block",
        reason=f"{label} risk is {risk}",
    )
