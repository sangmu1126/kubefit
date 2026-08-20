import math
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from evaluator.analysis import AnalysisTarget
from evaluator.safety import PatchEligibility, evaluate_patch_eligibility
from recommender import (
    CurrentResources,
    ObservedUsage,
    RecommendationPolicy,
    recommend_resources,
)
from recommender.models import RecommendationReadiness


class MetricReadinessProgress(BaseModel):
    sample_count: int = Field(ge=0)
    required_sample_count: int = Field(ge=1)
    observation_coverage: float = Field(ge=0, le=1)
    required_observation_coverage: float = Field(ge=0, le=1)
    pod_count: int = Field(ge=0)
    required_pod_count: int = Field(ge=1)


class ReplicaReadinessProgress(BaseModel):
    desired: int = Field(ge=1)
    available: int = Field(ge=0)
    observed: int = Field(ge=0)
    container_statuses: int = Field(ge=0)


class ObservationReadinessReport(BaseModel):
    schema_version: Literal[1] = 1
    target: AnalysisTarget
    workload_uid: str = Field(min_length=1)
    workload_created_at: datetime
    authorized_replica_set_count: int = Field(ge=1)
    identity_snapshot_enabled: bool
    observed_at: datetime
    status: Literal["eligible", "collecting", "blocked"]
    estimated_readiness_at: datetime | None
    observation_days: int = Field(ge=1)
    step_seconds: int = Field(ge=1)
    usage: MetricReadinessProgress
    throttling: MetricReadinessProgress
    replicas: ReplicaReadinessProgress
    recommendation_readiness: RecommendationReadiness
    patch_eligibility: PatchEligibility
    reasons: list[str]
    estimate_assumptions: list[str]

    @model_validator(mode="after")
    def estimate_matches_status(self) -> "ObservationReadinessReport":
        if self.status == "collecting" and self.estimated_readiness_at is None:
            raise ValueError("collecting readiness must include an estimate")
        if self.status != "collecting" and self.estimated_readiness_at is not None:
            raise ValueError("only collecting readiness can include an estimate")
        return self


def assess_observation_readiness(
    *,
    target: AnalysisTarget,
    current: CurrentResources,
    observed: ObservedUsage,
    observed_at: datetime,
    policy: RecommendationPolicy | None = None,
) -> ObservationReadinessReport:
    if observed_at.tzinfo is None:
        raise ValueError("readiness observation time must include timezone information")
    if observed.step_seconds is None:
        raise ValueError("readiness requires a Prometheus query step")
    if (
        not observed.workload_uid
        or observed.workload_created_at is None
        or observed.authorized_replica_set_count is None
        or observed.authorized_replica_set_count < 1
    ):
        raise ValueError("readiness requires complete workload identity evidence")
    if observed.workload_created_at.tzinfo is None:
        raise ValueError("workload creation time must include timezone information")

    policy = policy or RecommendationPolicy()
    recommendation = recommend_resources(current, observed, policy)
    eligibility = evaluate_patch_eligibility(recommendation)
    desired = observed.desired_replicas or 0
    if desired < 1:
        raise ValueError("readiness requires desired replicas")

    expected_per_pod = math.floor(
        observed.observation_days * 24 * 60 * 60 / observed.step_seconds
    ) + 1
    coverage_sample_count = math.ceil(
        expected_per_pod * desired * policy.minimum_observation_coverage
    )
    required_sample_count = max(policy.minimum_sample_count, coverage_sample_count)
    usage = MetricReadinessProgress(
        sample_count=observed.sample_count or 0,
        required_sample_count=required_sample_count,
        observation_coverage=observed.observation_coverage or 0,
        required_observation_coverage=policy.minimum_observation_coverage,
        pod_count=observed.metric_pod_count or 0,
        required_pod_count=desired,
    )
    throttling = MetricReadinessProgress(
        sample_count=observed.cpu_throttling_sample_count or 0,
        required_sample_count=required_sample_count,
        observation_coverage=observed.cpu_throttling_observation_coverage or 0,
        required_observation_coverage=policy.minimum_observation_coverage,
        pod_count=observed.cpu_throttling_pod_count or 0,
        required_pod_count=desired,
    )
    replicas = ReplicaReadinessProgress(
        desired=desired,
        available=observed.available_replicas or 0,
        observed=observed.observed_replicas or 0,
        container_statuses=observed.container_status_count or 0,
    )

    structural_blockers = _structural_blockers(usage, throttling, replicas)
    high_risk_blockers = []
    if recommendation.risk.oom == "high":
        high_risk_blockers.append("OOM risk is high")
    if recommendation.risk.cpu_throttling == "high":
        high_risk_blockers.append("CPU throttling risk is high")
    estimate: datetime | None = None
    assumptions: list[str] = []
    if recommendation.readiness.status == "ready":
        status = "eligible" if eligibility.status == "eligible" else "blocked"
        reasons = eligibility.blocking_reasons
    elif structural_blockers or high_risk_blockers:
        status = "blocked"
        reasons = structural_blockers + high_risk_blockers
    else:
        status = "collecting"
        additional_samples = max(
            0,
            usage.required_sample_count - usage.sample_count,
            throttling.required_sample_count - throttling.sample_count,
        )
        intervals = math.ceil(additional_samples / desired)
        estimate = observed_at + timedelta(
            seconds=intervals * observed.step_seconds
        )
        reasons = recommendation.readiness.reasons
        assumptions = [
            f"all {desired} desired replicas remain available and observed",
            f"usage and throttling continue producing one sample per replica every "
            f"{observed.step_seconds} seconds",
            "no rollout, OOMKilled state, or high-risk signal changes the decision",
        ]

    return ObservationReadinessReport(
        target=target,
        workload_uid=observed.workload_uid,
        workload_created_at=observed.workload_created_at,
        authorized_replica_set_count=observed.authorized_replica_set_count,
        identity_snapshot_enabled=observed.identity_snapshot_enabled,
        observed_at=observed_at,
        status=status,
        estimated_readiness_at=estimate,
        observation_days=observed.observation_days,
        step_seconds=observed.step_seconds,
        usage=usage,
        throttling=throttling,
        replicas=replicas,
        recommendation_readiness=recommendation.readiness,
        patch_eligibility=eligibility,
        reasons=reasons,
        estimate_assumptions=assumptions,
    )


def _structural_blockers(
    usage: MetricReadinessProgress,
    throttling: MetricReadinessProgress,
    replicas: ReplicaReadinessProgress,
) -> list[str]:
    reasons = []
    if len({replicas.desired, replicas.available, replicas.observed}) != 1:
        reasons.append(
            "replica counts require intervention: "
            f"desired={replicas.desired}, available={replicas.available}, "
            f"observed={replicas.observed}"
        )
    if replicas.container_statuses != replicas.desired:
        reasons.append(
            "container status coverage requires intervention: "
            f"statuses={replicas.container_statuses}, desired={replicas.desired}"
        )
    if usage.pod_count < usage.required_pod_count:
        reasons.append(
            "usage metric Pod coverage requires intervention: "
            f"metric_pods={usage.pod_count}, desired={usage.required_pod_count}"
        )
    if throttling.pod_count < throttling.required_pod_count:
        reasons.append(
            "throttling metric Pod coverage requires intervention: "
            f"metric_pods={throttling.pod_count}, desired={throttling.required_pod_count}"
        )
    return reasons
