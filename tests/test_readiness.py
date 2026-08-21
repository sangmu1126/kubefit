from datetime import UTC, datetime, timedelta

from evaluator import AnalysisTarget, assess_observation_readiness
from recommender import CurrentResources, ObservedUsage, RecommendationPolicy

OBSERVED_AT = datetime(2026, 8, 21, tzinfo=UTC)


def current_resources() -> CurrentResources:
    return CurrentResources(
        cpu_request_millicores=1000,
        cpu_limit_millicores=2000,
        memory_request_mib=2048,
        memory_limit_mib=4096,
    )


def observed_usage(**updates) -> ObservedUsage:
    values = {
        "cpu_p95_millicores": 230,
        "memory_p99_mib": 710,
        "cpu_max_millicores": 400,
        "memory_max_mib": 900,
        "observation_days": 1,
        "step_seconds": 300,
        "sample_count": 64,
        "observation_coverage": 0.111,
        "desired_replicas": 2,
        "available_replicas": 2,
        "observed_replicas": 2,
        "metric_pod_count": 2,
        "workload_uid": "deployment-uid",
        "workload_created_at": datetime(2026, 8, 20, tzinfo=UTC),
        "authorized_replica_set_count": 1,
        "identity_snapshot_enabled": True,
        "cpu_throttling_p95_percent": 0.2,
        "cpu_throttling_max_percent": 0.5,
        "cpu_throttling_sample_count": 64,
        "cpu_throttling_pod_count": 2,
        "cpu_throttling_observation_coverage": 0.111,
        "container_status_count": 2,
        "restart_count": 0,
        "oom_killed_count": 0,
    }
    values.update(updates)
    return ObservedUsage(**values)


def report_for(observed: ObservedUsage):
    return assess_observation_readiness(
        target=AnalysisTarget(namespace="demo", deployment="demo", container="api"),
        current=current_resources(),
        observed=observed,
        observed_at=OBSERVED_AT,
    )


def test_estimates_when_only_samples_and_coverage_need_time() -> None:
    report = report_for(observed_usage())

    assert report.status == "collecting"
    assert report.workload_uid == "deployment-uid"
    assert report.usage.required_sample_count == 405
    assert report.throttling.required_sample_count == 405
    assert report.estimated_readiness_at == OBSERVED_AT + timedelta(hours=14, minutes=15)
    assert report.estimate_assumptions
    assert report.patch_eligibility.status == "blocked"


def test_demo_window_requires_ninety_percent_of_one_hour() -> None:
    report = assess_observation_readiness(
        target=AnalysisTarget(namespace="demo", deployment="demo", container="api"),
        current=current_resources(),
        observed=observed_usage(
            observation_days=1 / 24,
            step_seconds=60,
            sample_count=80,
            observation_coverage=80 / 122,
            cpu_throttling_sample_count=80,
            cpu_throttling_observation_coverage=80 / 122,
        ),
        observed_at=OBSERVED_AT,
        policy=RecommendationPolicy(
            minimum_observation_coverage=0.9,
            minimum_sample_count=100,
        ),
    )

    assert report.usage.required_sample_count == 110
    assert report.throttling.required_sample_count == 110
    assert report.usage.required_observation_coverage == 0.9
    assert report.estimated_readiness_at == OBSERVED_AT + timedelta(minutes=15)


def test_reports_eligible_without_wait_estimate() -> None:
    report = report_for(
        observed_usage(
            sample_count=500,
            observation_coverage=0.9,
            cpu_throttling_sample_count=500,
            cpu_throttling_observation_coverage=0.9,
        )
    )

    assert report.status == "eligible"
    assert report.estimated_readiness_at is None
    assert report.patch_eligibility.status == "eligible"


def test_blocks_incomplete_metric_pod_coverage_without_estimate() -> None:
    report = report_for(observed_usage(metric_pod_count=1))

    assert report.status == "blocked"
    assert report.estimated_readiness_at is None
    assert report.reasons == [
        "usage metric Pod coverage requires intervention: metric_pods=1, desired=2"
    ]
    assert any(
        "usage metric Pod coverage is incomplete" in reason
        for reason in report.recommendation_readiness.reasons
    )


def test_blocks_high_risk_after_observation_is_ready() -> None:
    report = report_for(
        observed_usage(
            sample_count=500,
            observation_coverage=0.9,
            cpu_throttling_sample_count=500,
            cpu_throttling_observation_coverage=0.9,
            oom_killed_count=1,
        )
    )

    assert report.status == "blocked"
    assert report.recommendation_readiness.status == "ready"
    assert report.reasons == ["OOM risk is high"]


def test_does_not_estimate_away_observed_oom_during_collection() -> None:
    report = report_for(observed_usage(oom_killed_count=1))

    assert report.status == "blocked"
    assert report.estimated_readiness_at is None
    assert report.reasons == ["OOM risk is high"]
