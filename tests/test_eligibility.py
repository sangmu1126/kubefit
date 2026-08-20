import pytest

from evaluator import evaluate_patch_eligibility
from recommender.models import (
    RecommendationReadiness,
    ResourceRecommendation,
    ResourceValues,
    RiskAssessment,
)


def recommendation(
    *,
    readiness: str = "ready",
    oom: str = "low",
    throttling: str = "low",
) -> ResourceRecommendation:
    return ResourceRecommendation(
        recommended=ResourceValues(
            cpu_request_millicores=100,
            cpu_limit_millicores=200,
            memory_request_mib=128,
            memory_limit_mib=256,
        ),
        cpu_request_change_percent=-50,
        memory_request_change_percent=-50,
        readiness=RecommendationReadiness(
            status=readiness,
            reasons=[] if readiness == "ready" else ["observation coverage is too low"],
        ),
        risk=RiskAssessment(
            oom=oom,
            cpu_throttling=throttling,
            reasons=[],
        ),
        evidence=[],
    )


def test_marks_ready_low_risk_recommendation_eligible() -> None:
    result = evaluate_patch_eligibility(recommendation())

    assert result.status == "eligible"
    assert result.blocking_reasons == []
    assert result.warnings == []
    assert [check.status for check in result.checks] == ["pass", "pass", "pass"]


def test_blocks_insufficient_recommendation() -> None:
    result = evaluate_patch_eligibility(
        recommendation(readiness="insufficient_data")
    )

    assert result.status == "blocked"
    assert "observation coverage is too low" in result.blocking_reasons[0]
    assert result.checks[0].code == "recommendation_readiness"


@pytest.mark.parametrize(
    ("risk_name", "oom", "throttling"),
    [
        ("OOM", "high", "low"),
        ("CPU throttling", "low", "high"),
        ("OOM", "unknown", "low"),
        ("CPU throttling", "low", "unknown"),
    ],
)
def test_blocks_high_or_unknown_risk(
    risk_name: str, oom: str, throttling: str
) -> None:
    result = evaluate_patch_eligibility(
        recommendation(oom=oom, throttling=throttling)
    )

    assert result.status == "blocked"
    assert any(reason.startswith(risk_name) for reason in result.blocking_reasons)


@pytest.mark.parametrize(
    ("oom", "throttling", "warning_name"),
    [
        ("medium", "low", "OOM"),
        ("low", "medium", "CPU throttling"),
    ],
)
def test_medium_risk_is_eligible_with_warning(
    oom: str, throttling: str, warning_name: str
) -> None:
    result = evaluate_patch_eligibility(
        recommendation(oom=oom, throttling=throttling)
    )

    assert result.status == "eligible"
    assert any(warning.startswith(warning_name) for warning in result.warnings)
    assert "warning" in [check.status for check in result.checks]


def test_recommendation_direction_does_not_control_eligibility() -> None:
    candidate = recommendation()
    candidate.cpu_request_change_percent = 50
    candidate.memory_request_change_percent = 25

    result = evaluate_patch_eligibility(candidate)

    assert result.status == "eligible"
