import math
from dataclasses import dataclass

from recommender.models import (
    CurrentResources,
    ObservedUsage,
    ResourceRecommendation,
    ResourceValues,
    RiskAssessment,
)


@dataclass(frozen=True)
class RecommendationPolicy:
    safety_margin: float = 0.25
    cpu_limit_multiplier: float = 2.0
    memory_limit_multiplier: float = 1.5
    cpu_step_millicores: int = 10
    memory_step_mib: int = 16
    minimum_cpu_millicores: int = 10
    minimum_memory_mib: int = 32


def _round_up(value: float, step: int) -> int:
    return math.ceil(value / step) * step


def recommend_resources(
    current: CurrentResources,
    observed: ObservedUsage,
    policy: RecommendationPolicy | None = None,
) -> ResourceRecommendation:
    """Return a deterministic, explainable recommendation without cluster mutation."""
    policy = policy or RecommendationPolicy()
    margin_factor = 1 + policy.safety_margin

    cpu_request = max(
        policy.minimum_cpu_millicores,
        _round_up(observed.cpu_p95_millicores * margin_factor, policy.cpu_step_millicores),
    )
    memory_request = max(
        policy.minimum_memory_mib,
        _round_up(observed.memory_p99_mib * margin_factor, policy.memory_step_mib),
    )
    cpu_limit = _round_up(cpu_request * policy.cpu_limit_multiplier, policy.cpu_step_millicores)
    memory_limit = _round_up(
        memory_request * policy.memory_limit_multiplier, policy.memory_step_mib
    )

    cpu_change = (cpu_request / current.cpu_request_millicores - 1) * 100
    memory_change = (memory_request / current.memory_request_mib - 1) * 100

    coverage_is_sufficient = (
        observed.observation_coverage is not None and observed.observation_coverage >= 0.7
    )
    oom_headroom = (
        memory_limit / max(observed.memory_max_mib, 1)
        if observed.memory_max_mib is not None
        else None
    )
    cpu_headroom = (
        cpu_limit / max(observed.cpu_max_millicores, 1)
        if observed.cpu_max_millicores is not None
        else None
    )
    oom_risk = _risk_from_headroom(oom_headroom, coverage_is_sufficient)
    throttle_risk = _risk_from_headroom(cpu_headroom, coverage_is_sufficient)

    risk_reasons = []
    if observed.observation_coverage is None:
        risk_reasons.append("observation coverage was not provided")
    elif not coverage_is_sufficient:
        risk_reasons.append(
            f"observation coverage is {observed.observation_coverage:.1%}; at least 70% is required"
        )
    risk_reasons.extend(
        [
            _headroom_reason("memory limit", oom_headroom, "observed maximum"),
            _headroom_reason("CPU limit", cpu_headroom, "observed maximum"),
        ]
    )

    evidence = [
        f"CPU request uses {observed.observation_days}-day P95 plus "
        f"{policy.safety_margin:.0%} safety margin",
        f"memory request uses {observed.observation_days}-day P99 plus "
        f"{policy.safety_margin:.0%} safety margin",
        "values are rounded upward to scheduler-friendly units",
    ]
    if observed.sample_count is not None and observed.observation_coverage is not None:
        evidence.append(
            f"{observed.sample_count} paired samples provide "
            f"{observed.observation_coverage:.1%} observation coverage"
        )

    return ResourceRecommendation(
        recommended=ResourceValues(
            cpu_request_millicores=cpu_request,
            cpu_limit_millicores=cpu_limit,
            memory_request_mib=memory_request,
            memory_limit_mib=memory_limit,
        ),
        cpu_request_change_percent=round(cpu_change, 1),
        memory_request_change_percent=round(memory_change, 1),
        risk=RiskAssessment(
            oom=oom_risk,
            cpu_throttling=throttle_risk,
            reasons=risk_reasons,
        ),
        evidence=evidence,
    )


def _risk_from_headroom(headroom: float | None, coverage_is_sufficient: bool) -> str:
    if headroom is None or not coverage_is_sufficient:
        return "unknown"
    if headroom >= 1.25:
        return "low"
    if headroom >= 1.05:
        return "medium"
    return "high"


def _headroom_reason(label: str, headroom: float | None, baseline: str) -> str:
    if headroom is None:
        return f"{label} risk is unknown because a {baseline} was not provided"
    return f"{label} provides {headroom:.2f}x headroom over {baseline}"
