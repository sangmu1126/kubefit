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

    current_request_score = current.cpu_request_millicores + current.memory_request_mib
    recommended_request_score = cpu_request + memory_request
    savings = max(0.0, (1 - recommended_request_score / current_request_score) * 100)

    oom_headroom = memory_limit / max(observed.memory_p99_mib, 1)
    cpu_headroom = cpu_limit / max(observed.cpu_p95_millicores, 1)
    oom_risk = "low" if oom_headroom >= 1.5 else "medium" if oom_headroom >= 1.2 else "high"
    throttle_risk = "low" if cpu_headroom >= 2 else "medium" if cpu_headroom >= 1.5 else "high"

    return ResourceRecommendation(
        recommended=ResourceValues(
            cpu_request_millicores=cpu_request,
            cpu_limit_millicores=cpu_limit,
            memory_request_mib=memory_request,
            memory_limit_mib=memory_limit,
        ),
        estimated_request_reduction_percent=round(savings, 1),
        risk=RiskAssessment(
            oom=oom_risk,
            cpu_throttling=throttle_risk,
            reasons=[
                f"memory limit provides {oom_headroom:.2f}x headroom over observed P99",
                f"CPU limit provides {cpu_headroom:.2f}x headroom over observed P95",
            ],
        ),
        evidence=[
            f"CPU request uses {observed.observation_days}-day P95 plus "
            f"{policy.safety_margin:.0%} safety margin",
            f"memory request uses {observed.observation_days}-day P99 plus "
            f"{policy.safety_margin:.0%} safety margin",
            "values are rounded upward to scheduler-friendly units",
        ],
    )
