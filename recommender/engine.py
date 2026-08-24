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
    oom_risk = _oom_risk(observed, oom_headroom, evidence_is_sufficient)
    throttle_risk = _cpu_throttling_risk(
        observed.cpu_throttling_p95_percent,
        cpu_headroom,
        evidence_is_sufficient,
    )

    risk_reasons = readiness_reasons.copy()
    risk_reasons.extend(
        [
            _runtime_status_reason(observed),
            _throttling_reason(observed),
            _headroom_reason("memory limit", oom_headroom, "observed maximum"),
            _headroom_reason("CPU limit", cpu_headroom, "observed maximum"),
        ]
    )

    observation_window = _observation_window_label(observed.observation_days)
    evidence = [
        f"CPU request uses {observation_window} P95 plus "
        f"{policy.safety_margin:.0%} safety margin",
        f"memory request uses {observation_window} P99 plus "
        f"{policy.safety_margin:.0%} safety margin",
        "values are rounded upward to scheduler-friendly units",
    ]
    if observed.observation_days < 1:
        evidence.append(
            "short observation window is for a controlled demo, not production traffic"
        )
    if observed.sample_count is not None and observed.observation_coverage is not None:
        sample_label = "sample" if observed.sample_count == 1 else "samples"
        provide_verb = "provides" if observed.sample_count == 1 else "provide"
        evidence.append(
            f"{observed.sample_count} metric {sample_label} {provide_verb} "
            f"{observed.observation_coverage:.1%} observation coverage"
        )
    if observed.metric_pod_count is not None:
        identity_label = "identity" if observed.metric_pod_count == 1 else "identities"
        evidence.append(
            f"metrics include {observed.metric_pod_count} current Pod {identity_label}"
        )
    if observed.minimum_current_pod_sample_count is not None:
        evidence.append(
            "least-observed current Pod contributes "
            f"{observed.minimum_current_pod_sample_count} paired CPU/memory samples"
        )
    if observed.step_seconds is not None:
        evidence.append(f"Prometheus range query step is {observed.step_seconds} seconds")
    if observed.workload_uid is not None:
        evidence.append(f"workload identity is Kubernetes UID {observed.workload_uid}")
    if observed.history_clipped and observed.workload_created_at is not None:
        evidence.append(
            "metric history starts at current workload creation time "
            f"{observed.workload_created_at.isoformat()}"
        )
    if observed.authorized_replica_set_count is not None:
        source = "identity snapshot" if observed.identity_snapshot_enabled else "Kubernetes API"
        replica_set_label = (
            "ReplicaSet" if observed.authorized_replica_set_count == 1 else "ReplicaSets"
        )
        evidence.append(
            f"{source} authorizes {observed.authorized_replica_set_count} {replica_set_label}"
        )
    if observed.cpu_throttling_p95_percent is not None:
        evidence.append(_throttling_reason(observed))
    if observed.container_status_count is not None:
        evidence.append(_runtime_status_reason(observed))

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


def _observation_window_label(observation_days: float) -> str:
    if observation_days >= 1:
        displayed_days = f"{observation_days:g}"
        return f"{displayed_days}-day"
    hours = observation_days * 24
    if hours >= 1:
        return f"{hours:g}-hour"
    return f"{hours * 60:g}-minute"


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
    required_per_pod = _minimum_samples_per_current_pod(observed, policy)
    if (
        observed.minimum_current_pod_sample_count is not None
        and observed.minimum_current_pod_sample_count < required_per_pod
    ):
        reasons.append(
            "least-observed current Pod has "
            f"{observed.minimum_current_pod_sample_count} paired usage samples; "
            f"at least {required_per_pod} are required per current Pod"
        )
    if (
        observed.metric_pod_count is not None
        and observed.desired_replicas is not None
        and observed.metric_pod_count < observed.desired_replicas
    ):
        reasons.append(
            "usage metric Pod coverage is incomplete: "
            f"metric_pods={observed.metric_pod_count}, "
            f"desired={observed.desired_replicas}"
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

    if observed.cpu_throttling_p95_percent is None:
        reasons.append("CPU throttling metrics were not available")
    elif observed.cpu_throttling_sample_count is None:
        reasons.append("CPU throttling sample count was not provided")
    elif observed.cpu_throttling_sample_count < policy.minimum_sample_count:
        reasons.append(
            f"CPU throttling sample count is {observed.cpu_throttling_sample_count}; "
            f"at least {policy.minimum_sample_count} is required"
        )
    if (
        observed.minimum_current_pod_throttling_sample_count is not None
        and observed.minimum_current_pod_throttling_sample_count < required_per_pod
    ):
        reasons.append(
            "least-observed current Pod has "
            f"{observed.minimum_current_pod_throttling_sample_count} throttling samples; "
            f"at least {required_per_pod} are required per current Pod"
        )
    if observed.cpu_throttling_observation_coverage is None:
        reasons.append("CPU throttling observation coverage was not provided")
    elif (
        observed.cpu_throttling_observation_coverage
        < policy.minimum_observation_coverage
    ):
        reasons.append(
            "CPU throttling observation coverage is "
            f"{observed.cpu_throttling_observation_coverage:.1%}; at least "
            f"{policy.minimum_observation_coverage:.0%} is required"
        )
    if observed.cpu_throttling_pod_count is None:
        reasons.append("CPU throttling Pod count was not provided")
    elif (
        observed.desired_replicas is not None
        and observed.cpu_throttling_pod_count < observed.desired_replicas
    ):
        reasons.append(
            "CPU throttling Pod coverage is incomplete: "
            f"metric_pods={observed.cpu_throttling_pod_count}, "
            f"desired={observed.desired_replicas}"
        )

    status_values = (
        observed.container_status_count,
        observed.restart_count,
        observed.oom_killed_count,
    )
    if any(value is None for value in status_values):
        reasons.append("Kubernetes container status signals were not all provided")
    elif (
        observed.desired_replicas is not None
        and observed.container_status_count != observed.desired_replicas
    ):
        reasons.append(
            "target container status coverage is incomplete: "
            f"statuses={observed.container_status_count}, "
            f"desired={observed.desired_replicas}"
        )
    return reasons


def _minimum_samples_per_current_pod(
    observed: ObservedUsage, policy: RecommendationPolicy
) -> int:
    return math.ceil(policy.minimum_sample_count / (observed.desired_replicas or 1))


def _risk_from_headroom(headroom: float | None, evidence_is_sufficient: bool) -> str:
    if headroom is None or not evidence_is_sufficient:
        return "unknown"
    if headroom >= 1.25:
        return "low"
    if headroom >= 1.05:
        return "medium"
    return "high"


def _oom_risk(
    observed: ObservedUsage,
    headroom: float | None,
    evidence_is_sufficient: bool,
) -> str:
    if observed.oom_killed_count is not None and observed.oom_killed_count > 0:
        return "high"
    return _risk_from_headroom(headroom, evidence_is_sufficient)


def _cpu_throttling_risk(
    throttling_p95_percent: float | None,
    headroom: float | None,
    evidence_is_sufficient: bool,
) -> str:
    if throttling_p95_percent is None:
        return "unknown"
    if throttling_p95_percent >= 10:
        return "high"
    if throttling_p95_percent >= 1:
        return "medium"
    return _risk_from_headroom(headroom, evidence_is_sufficient)


def _runtime_status_reason(observed: ObservedUsage) -> str:
    if (
        observed.container_status_count is None
        or observed.restart_count is None
        or observed.oom_killed_count is None
    ):
        return "Kubernetes target container status was not available"
    return (
        f"Kubernetes reports {observed.restart_count} restarts and "
        f"{observed.oom_killed_count} OOMKilled states across "
        f"{observed.container_status_count} target container statuses"
    )


def _throttling_reason(observed: ObservedUsage) -> str:
    if observed.cpu_throttling_p95_percent is None:
        return "CPU throttling metrics were not available"
    sample_count = observed.cpu_throttling_sample_count or 0
    pod_count = observed.cpu_throttling_pod_count or 0
    coverage = observed.cpu_throttling_observation_coverage or 0
    return (
        f"CPU throttled-period P95 is {observed.cpu_throttling_p95_percent:.2f}% "
        f"across {sample_count} samples and {pod_count} Pod identities, providing "
        f"{coverage:.1%} observation coverage"
    )


def _headroom_reason(label: str, headroom: float | None, baseline: str) -> str:
    if headroom is None:
        return f"{label} risk is unknown because a {baseline} was not provided"
    return f"{label} provides {headroom:.2f}x headroom over {baseline}"
