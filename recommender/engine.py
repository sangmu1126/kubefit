import math
from dataclasses import dataclass

from recommender.models import (
    CurrentResources,
    ObservedUsage,
    RecommendationReadiness,
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
    minimum_observation_coverage: float = 0.7
    minimum_sample_count: int = 100


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

    readiness_reasons = _readiness_reasons(observed, policy)
    evidence_is_sufficient = not readiness_reasons
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
    oom_risk = _risk_from_headroom(oom_headroom, evidence_is_sufficient)
    throttle_risk = _risk_from_headroom(cpu_headroom, evidence_is_sufficient)

    risk_reasons = readiness_reasons.copy()
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
            f"{observed.sample_count} metric samples provide "
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
        readiness=RecommendationReadiness(
            status="ready" if evidence_is_sufficient else "insufficient_data",
            reasons=readiness_reasons,
        ),
        risk=RiskAssessment(
            oom=oom_risk,
            cpu_throttling=throttle_risk,
            reasons=risk_reasons,
        ),
        evidence=evidence,
    )


def _readiness_reasons(
    observed: ObservedUsage, policy: RecommendationPolicy
) -> list[str]:
    reasons = []
    if observed.observation_coverage is None:
        reasons.append("observation coverage was not provided")
    elif observed.observation_coverage < policy.minimum_observation_coverage:
        reasons.append(
            f"observation coverage is {observed.observation_coverage:.1%}; at least "
            f"{policy.minimum_observation_coverage:.0%} is required"
        )

    if observed.sample_count is None:
        reasons.append("sample count was not provided")
    elif observed.sample_count < policy.minimum_sample_count:
        reasons.append(
            f"sample count is {observed.sample_count}; at least "
            f"{policy.minimum_sample_count} is required"
        )

    replica_values = (
        observed.desired_replicas,
        observed.available_replicas,
        observed.observed_replicas,
    )
    if any(value is None for value in replica_values):
        reasons.append("desired, available, and observed replica counts were not all provided")
    elif len(set(replica_values)) != 1:
        desired, available, metric_pods = replica_values
        reasons.append(
            "replica counts are unstable: "
            f"desired={desired}, available={available}, observed={metric_pods}"
        )
    return reasons


def _risk_from_headroom(headroom: float | None, evidence_is_sufficient: bool) -> str:
    if headroom is None or not evidence_is_sufficient:
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
