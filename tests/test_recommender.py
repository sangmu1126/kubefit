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
        ObservedUsage(cpu_p95_millicores=230, memory_p99_mib=710),
    )

    assert result.recommended.cpu_request_millicores == 290
    assert result.recommended.memory_request_mib == 896
    assert result.recommended.cpu_limit_millicores == 580
    assert result.recommended.memory_limit_mib == 1344
    assert result.estimated_request_reduction_percent == 61.1
    assert result.risk.oom == "low"
    assert result.risk.cpu_throttling == "low"
    assert len(result.evidence) == 3


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


def test_rejects_limit_below_request() -> None:
    with pytest.raises(ValidationError):
        CurrentResources(
            cpu_request_millicores=500,
            cpu_limit_millicores=100,
            memory_request_mib=128,
            memory_limit_mib=256,
        )
