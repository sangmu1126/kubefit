import pytest
from pydantic import ValidationError

from recommender import CurrentResources, ObservedUsage, recommend_resources


def test_recommends_rounded_resources_with_explanations() -> None:
    result = recommend_resources(
        CurrentResources(
            cpu_request_millicores=1000,
            cpu_limit_millicores=2000,
            memory_request_mib=2048,
            memory_limit_mib=4096,
        ),
        ObservedUsage(
            cpu_p95_millicores=230,
            memory_p99_mib=710,
            cpu_max_millicores=400,
            memory_max_mib=900,
            sample_count=1900,
            observation_coverage=0.95,
            desired_replicas=2,
            available_replicas=2,
            observed_replicas=2,
        ),
    )

    assert result.recommended.cpu_request_millicores == 290
    assert result.recommended.memory_request_mib == 896
    assert result.recommended.cpu_limit_millicores == 580
    assert result.recommended.memory_limit_mib == 1344
    assert result.cpu_request_change_percent == -71.0
    assert result.memory_request_change_percent == -56.2
    assert result.readiness.status == "ready"
    assert result.readiness.reasons == []
    assert result.risk.oom == "low"
    assert result.risk.cpu_throttling == "low"
    assert len(result.evidence) == 4


def test_enforces_minimum_resources_for_idle_workloads() -> None:
    result = recommend_resources(
        CurrentResources(
            cpu_request_millicores=100,
            cpu_limit_millicores=200,
            memory_request_mib=128,
            memory_limit_mib=256,
        ),
        ObservedUsage(cpu_p95_millicores=0, memory_p99_mib=0),
    )

    assert result.recommended.cpu_request_millicores == 10
    assert result.recommended.memory_request_mib == 32
    assert result.risk.oom == "unknown"
    assert result.risk.cpu_throttling == "unknown"


def test_marks_risk_unknown_when_observation_coverage_is_insufficient() -> None:
    result = recommend_resources(
        CurrentResources(
            cpu_request_millicores=1000,
            cpu_limit_millicores=2000,
            memory_request_mib=2048,
            memory_limit_mib=4096,
        ),
        ObservedUsage(
            cpu_p95_millicores=230,
            memory_p99_mib=710,
            cpu_max_millicores=400,
            memory_max_mib=900,
            sample_count=400,
            observation_coverage=0.2,
            desired_replicas=2,
            available_replicas=2,
            observed_replicas=2,
        ),
    )

    assert result.risk.oom == "unknown"
    assert result.risk.cpu_throttling == "unknown"
    assert "at least 70%" in result.risk.reasons[0]
    assert result.readiness.status == "insufficient_data"


def test_blocks_recommendation_while_replicas_are_unstable() -> None:
    result = recommend_resources(
        CurrentResources(
            cpu_request_millicores=1000,
            cpu_limit_millicores=2000,
            memory_request_mib=2048,
            memory_limit_mib=4096,
        ),
        ObservedUsage(
            cpu_p95_millicores=230,
            memory_p99_mib=710,
            cpu_max_millicores=400,
            memory_max_mib=900,
            sample_count=1900,
            observation_coverage=0.95,
            desired_replicas=3,
            available_replicas=2,
            observed_replicas=2,
        ),
    )

    assert result.readiness.status == "insufficient_data"
    assert "desired=3, available=2, observed=2" in result.readiness.reasons[0]
    assert result.risk.oom == "unknown"


def test_formats_singular_metric_evidence() -> None:
    result = recommend_resources(
        CurrentResources(
            cpu_request_millicores=100,
            cpu_limit_millicores=200,
            memory_request_mib=128,
            memory_limit_mib=256,
        ),
        ObservedUsage(
            cpu_p95_millicores=10,
            memory_p99_mib=32,
            sample_count=1,
            observation_coverage=0.01,
            metric_pod_count=1,
            authorized_replica_set_count=1,
        ),
    )

    assert any("1 metric sample provides" in item for item in result.evidence)
    assert any("1 Pod identity across" in item for item in result.evidence)
    assert any("authorizes 1 ReplicaSet" in item for item in result.evidence)


def test_rejects_limit_below_request() -> None:
    with pytest.raises(ValidationError):
        CurrentResources(
            cpu_request_millicores=500,
            cpu_limit_millicores=100,
            memory_request_mib=128,
            memory_limit_mib=256,
        )
