from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from evaluator.safety import PatchEligibility, evaluate_patch_eligibility
from recommender import CurrentResources, ObservedUsage, ResourceRecommendation
from recommender.engine import recommend_resources

_MONEY_QUANTUM = Decimal("0.000001")
_PERCENT_QUANTUM = Decimal("0.1")


class RequestResources(Protocol):
    cpu_request_millicores: int
    memory_request_mib: int


class CostAssumptions(BaseModel):
    currency: Literal["USD"] = "USD"
    cpu_core_hour_usd: Decimal = Field(gt=0)
    memory_gib_hour_usd: Decimal = Field(gt=0)
    monthly_hours: Decimal = Field(default=Decimal("730"), gt=0, le=744)
    price_source: str = Field(min_length=1)

    @field_validator("price_source")
    @classmethod
    def price_source_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("price_source must not be blank")
        return value


class MonthlyCost(BaseModel):
    cpu_usd: Decimal
    memory_usd: Decimal
    total_usd: Decimal


class CostComparison(BaseModel):
    assumptions: CostAssumptions
    replica_count: int = Field(gt=0)
    basis: Literal["resource_requests"] = "resource_requests"
    current: MonthlyCost
    recommended: MonthlyCost
    monthly_delta_usd: Decimal
    savings_percent: Decimal
    caveats: list[str]


class EvaluationResult(BaseModel):
    recommendation: ResourceRecommendation
    cost: CostComparison
    patch_eligibility: PatchEligibility


def _monthly_cost_exact(
    resources: RequestResources,
    assumptions: CostAssumptions,
    replica_count: int,
) -> tuple[Decimal, Decimal]:
    cpu_cores = Decimal(resources.cpu_request_millicores) / Decimal(1000)
    memory_gib = Decimal(resources.memory_request_mib) / Decimal(1024)
    scale = assumptions.monthly_hours * replica_count
    return (
        cpu_cores * assumptions.cpu_core_hour_usd * scale,
        memory_gib * assumptions.memory_gib_hour_usd * scale,
    )


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _breakdown(cpu: Decimal, memory: Decimal) -> MonthlyCost:
    return MonthlyCost(
        cpu_usd=_round_money(cpu),
        memory_usd=_round_money(memory),
        total_usd=_round_money(cpu + memory),
    )


def compare_request_costs(
    current: RequestResources,
    recommended: RequestResources,
    assumptions: CostAssumptions,
    replica_count: int,
) -> CostComparison:
    if replica_count < 1:
        raise ValueError("replica_count must be at least 1")

    current_cpu, current_memory = _monthly_cost_exact(
        current, assumptions, replica_count
    )
    recommended_cpu, recommended_memory = _monthly_cost_exact(
        recommended, assumptions, replica_count
    )
    current_total = current_cpu + current_memory
    recommended_total = recommended_cpu + recommended_memory
    delta = recommended_total - current_total
    savings_percent = (current_total - recommended_total) / current_total * 100

    return CostComparison(
        assumptions=assumptions,
        replica_count=replica_count,
        current=_breakdown(current_cpu, current_memory),
        recommended=_breakdown(recommended_cpu, recommended_memory),
        monthly_delta_usd=_round_money(delta),
        savings_percent=savings_percent.quantize(
            _PERCENT_QUANTUM, rounding=ROUND_HALF_UP
        ),
        caveats=[
            "projection uses Kubernetes resource requests, not observed usage or limits",
            "request-cost savings do not guarantee an equal cloud invoice reduction",
            "taxes, discounts, node fragmentation, and autoscaling replica-hours are excluded",
        ],
    )


def evaluate_resources(
    current: CurrentResources,
    observed: ObservedUsage,
    assumptions: CostAssumptions,
    replica_count: int,
) -> EvaluationResult:
    recommendation = recommend_resources(current, observed)
    cost = compare_request_costs(
        current, recommendation.recommended, assumptions, replica_count
    )
    return EvaluationResult(
        recommendation=recommendation,
        cost=cost,
        patch_eligibility=evaluate_patch_eligibility(recommendation),
    )
