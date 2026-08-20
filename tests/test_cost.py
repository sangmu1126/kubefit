from decimal import Decimal

import pytest
from pydantic import ValidationError

from evaluator import CostAssumptions, compare_request_costs, evaluate_resources
from recommender import CurrentResources, ObservedUsage
from recommender.models import ResourceValues


def assumptions() -> CostAssumptions:
    return CostAssumptions(
        cpu_core_hour_usd=Decimal("0.04"),
        memory_gib_hour_usd=Decimal("0.005"),
        monthly_hours=Decimal("730"),
        price_source="example://local-model",
    )


def test_compares_monthly_request_costs_by_component() -> None:
    result = compare_request_costs(
        CurrentResources(
            cpu_request_millicores=1000,
            cpu_limit_millicores=2000,
            memory_request_mib=2048,
            memory_limit_mib=4096,
        ),
        ResourceValues(
            cpu_request_millicores=500,
            cpu_limit_millicores=1000,
            memory_request_mib=1024,
            memory_limit_mib=1536,
        ),
        assumptions(),
        replica_count=2,
    )

    assert result.current.cpu_usd == Decimal("58.400000")
    assert result.current.memory_usd == Decimal("14.600000")
    assert result.current.total_usd == Decimal("73.000000")
    assert result.recommended.total_usd == Decimal("36.500000")
    assert result.monthly_delta_usd == Decimal("-36.500000")
    assert result.savings_percent == Decimal("50.0")
    assert result.assumptions.price_source == "example://local-model"
    assert result.basis == "resource_requests"


def test_reports_negative_savings_for_an_upsize() -> None:
    result = compare_request_costs(
        ResourceValues(
            cpu_request_millicores=100,
            cpu_limit_millicores=200,
            memory_request_mib=128,
            memory_limit_mib=256,
        ),
        ResourceValues(
            cpu_request_millicores=200,
            cpu_limit_millicores=400,
            memory_request_mib=256,
            memory_limit_mib=512,
        ),
        assumptions(),
        replica_count=1,
    )

    assert result.monthly_delta_usd > 0
    assert result.savings_percent == Decimal("-100.0")


def test_keeps_cost_projection_separate_from_recommendation_readiness() -> None:
    result = evaluate_resources(
        CurrentResources(
            cpu_request_millicores=1000,
            cpu_limit_millicores=2000,
            memory_request_mib=2048,
            memory_limit_mib=4096,
        ),
        ObservedUsage(cpu_p95_millicores=230, memory_p99_mib=710),
        assumptions(),
        replica_count=2,
    )

    assert result.recommendation.readiness.status == "insufficient_data"
    assert result.cost.savings_percent > 0
    assert result.patch_eligibility.status == "blocked"
    assert any("do not guarantee" in caveat for caveat in result.cost.caveats)


@pytest.mark.parametrize(
    "field",
    ["cpu_core_hour_usd", "memory_gib_hour_usd", "price_source"],
)
def test_rejects_missing_cost_assumptions(field: str) -> None:
    values = {
        "cpu_core_hour_usd": Decimal("0.04"),
        "memory_gib_hour_usd": Decimal("0.005"),
        "price_source": "example://local-model",
    }
    del values[field]

    with pytest.raises(ValidationError):
        CostAssumptions(**values)


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-0.01")])
def test_rejects_non_positive_prices(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        CostAssumptions(
            cpu_core_hour_usd=value,
            memory_gib_hour_usd=Decimal("0.005"),
            price_source="example://local-model",
        )


def test_rejects_blank_price_source() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        CostAssumptions(
            cpu_core_hour_usd=Decimal("0.04"),
            memory_gib_hour_usd=Decimal("0.005"),
            price_source="   ",
        )


def test_rejects_zero_replicas() -> None:
    resources = ResourceValues(
        cpu_request_millicores=100,
        cpu_limit_millicores=200,
        memory_request_mib=128,
        memory_limit_mib=256,
    )

    with pytest.raises(ValueError, match="at least 1"):
        compare_request_costs(resources, resources, assumptions(), replica_count=0)
