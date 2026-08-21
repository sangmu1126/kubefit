from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks import (
    BenchmarkMeasurement,
    K6RunSummary,
    compare_benchmarks,
)

PROPOSAL_ID = "proposal-0123456789abcdef0123456789abcdef"


def test_k6_profile_exports_every_trend_used_by_handle_summary() -> None:
    profile = (
        Path(__file__).parents[1] / "benchmarks" / "k6" / "resource_profile.js"
    ).read_text()

    assert (
        'summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", '
        '"p(99)"]' in profile
    )


def measurement(run_variant: str, **overrides: object) -> BenchmarkMeasurement:
    values: dict[str, object] = {
        "schema_version": 1,
        "profile_version": "kubefit-load-v1",
        "proposal_id": PROPOSAL_ID,
        "variant": run_variant,
        "dropped_iterations": 0,
        "steady": phase(300, 100, 110),
        "spike": phase(750, 200, 220),
        "recovery": phase(300, 120, 130),
        "runtime": {
            "cpu_throttling_p95_percent": 1,
            "oom_killed_count": 0,
            "restart_count": 0,
            "traffic_spike_recovery_seconds": 10,
        },
        "provenance": provenance(),
        "request_cost_usd": Decimal("10"),
    }
    values.update(overrides)
    return BenchmarkMeasurement.model_validate(values)


def phase(
    expected: int,
    p95: float,
    p99: float,
    *,
    completed: int | None = None,
    requests: int | None = None,
    error_rate: float = 0,
) -> dict[str, object]:
    return {
        "expected_iterations": expected,
        "completed_iterations": expected if completed is None else completed,
        "requests": expected if requests is None else requests,
        "error_rate": error_rate,
        "latency_p95_ms": p95,
        "latency_p99_ms": p99,
    }


def check_status(verdict, code: str) -> str:
    return next(check.status for check in verdict.checks if check.code == code)


def test_passes_safe_candidate_and_reports_cost_separately() -> None:
    before = measurement("before")
    after = measurement(
        "after",
        steady=phase(300, 105, 115),
        spike=phase(750, 220, 240),
        request_cost_usd=Decimal("7.5"),
    )

    verdict = compare_benchmarks(before, after)

    assert verdict.status == "pass"
    assert verdict.failures == []
    assert verdict.cost_change_percent == Decimal("-25.000")


@pytest.mark.parametrize(
    ("percentile", "before_value", "after_value", "expected_status"),
    [
        ("p95", 100, 110, "pass"),
        ("p95", 100, 110.001, "fail"),
        ("p95", 0, 0, "pass"),
        ("p95", 0, 1, "fail"),
        ("p99", 100, 110, "pass"),
        ("p99", 100, 110.001, "fail"),
    ],
)
def test_steady_latency_boundary(
    percentile: str,
    before_value: float,
    after_value: float,
    expected_status: str,
) -> None:
    before_values = (before_value, before_value) if percentile == "p99" else (before_value, 200)
    after_values = (after_value, after_value) if percentile == "p99" else (after_value, 200)
    before = measurement("before", steady=phase(300, *before_values))
    after = measurement("after", steady=phase(300, *after_values))

    verdict = compare_benchmarks(before, after)

    assert check_status(verdict, f"steady_latency_{percentile}") == expected_status


@pytest.mark.parametrize(
    ("percentile", "after_value", "expected_status"),
    [
        ("p95", 230, "pass"),
        ("p95", 230.001, "fail"),
        ("p99", 253, "pass"),
        ("p99", 253.001, "fail"),
    ],
)
def test_spike_latency_boundary(percentile: str, after_value: float, expected_status: str) -> None:
    before = measurement("before", spike=phase(750, 200, 220))
    after_values = (after_value, 260) if percentile == "p95" else (200, after_value)
    after = measurement("after", spike=phase(750, *after_values))

    verdict = compare_benchmarks(before, after)

    assert check_status(verdict, f"spike_latency_{percentile}") == expected_status


@pytest.mark.parametrize(
    ("before_error", "after_error", "code", "expected_status"),
    [
        (0, 0.01, "steady_error_rate_after", "pass"),
        (0, 0.01001, "steady_error_rate_after", "fail"),
        (0.004, 0.009, "steady_error_rate_increase", "pass"),
        (0.004, 0.00901, "steady_error_rate_increase", "fail"),
    ],
)
def test_error_rate_boundaries(
    before_error: float, after_error: float, code: str, expected_status: str
) -> None:
    before = measurement("before", steady=phase(300, 100, 110, error_rate=before_error))
    after = measurement("after", steady=phase(300, 100, 110, error_rate=after_error))

    verdict = compare_benchmarks(before, after)

    assert check_status(verdict, code) == expected_status


@pytest.mark.parametrize(
    ("before_throttling", "after_throttling", "code", "expected_status"),
    [
        (4, 5, "throttling_after", "pass"),
        (4, 5.001, "throttling_after", "fail"),
        (3, 4, "throttling_increase", "pass"),
        (3, 4.001, "throttling_increase", "fail"),
    ],
)
def test_throttling_boundaries(
    before_throttling: float,
    after_throttling: float,
    code: str,
    expected_status: str,
) -> None:
    before = measurement(
        "before",
        runtime=runtime(cpu_throttling_p95_percent=before_throttling),
    )
    after = measurement(
        "after",
        runtime=runtime(cpu_throttling_p95_percent=after_throttling),
    )

    verdict = compare_benchmarks(before, after)

    assert check_status(verdict, code) == expected_status


def test_new_oom_fails_and_restart_increase_warns() -> None:
    before = measurement("before", runtime=runtime())
    after = measurement("after", runtime=runtime(oom_killed_count=1, restart_count=2))

    verdict = compare_benchmarks(before, after)

    assert verdict.status == "fail"
    assert check_status(verdict, "new_oom_killed") == "fail"
    assert check_status(verdict, "new_restarts") == "warning"


def test_candidate_oom_fails_even_when_baseline_also_had_one() -> None:
    verdict = compare_benchmarks(
        measurement("before", runtime=runtime(oom_killed_count=1)),
        measurement("after", runtime=runtime(oom_killed_count=1)),
    )

    assert verdict.status == "fail"
    assert check_status(verdict, "new_oom_killed") == "fail"


def test_incomplete_candidate_recovery_fails() -> None:
    verdict = compare_benchmarks(
        measurement("before"),
        measurement("after", runtime=runtime(traffic_spike_recovered=False)),
    )

    assert verdict.status == "fail"
    assert check_status(verdict, "traffic_spike_recovered") == "fail"


def test_incomplete_baseline_recovery_invalidates_comparison() -> None:
    verdict = compare_benchmarks(
        measurement("before", runtime=runtime(traffic_spike_recovered=False)),
        measurement("after"),
    )

    assert verdict.status == "invalid"
    assert check_status(verdict, "baseline_recovered") == "invalid"


@pytest.mark.parametrize(("after_recovery", "expected_status"), [(12, "pass"), (12.001, "fail")])
def test_recovery_boundary(after_recovery: float, expected_status: str) -> None:
    before = measurement("before", runtime=runtime(traffic_spike_recovery_seconds=10))
    after = measurement("after", runtime=runtime(traffic_spike_recovery_seconds=after_recovery))

    verdict = compare_benchmarks(before, after)

    assert check_status(verdict, "traffic_spike_recovery") == expected_status


@pytest.mark.parametrize(
    "after_overrides",
    [
        {"proposal_id": "proposal-fedcba9876543210fedcba9876543210"},
        {"profile_version": "kubefit-load-v2"},
        {"variant": "before"},
        {"dropped_iterations": 1},
        {"steady": phase(300, 100, 110, completed=299)},
        {"steady": phase(300, 100, 110, completed=301)},
        {"steady": phase(301, 100, 110, completed=301)},
        {"steady": phase(300, 100, 110, requests=299)},
    ],
)
def test_rejects_non_comparable_runs(after_overrides: dict[str, object]) -> None:
    verdict = compare_benchmarks(measurement("before"), measurement("after", **after_overrides))

    assert verdict.status == "invalid"
    assert verdict.invalid_reasons
    assert not any(check.status == "fail" for check in verdict.checks)


def test_accepts_matching_boundary_overshoot_above_fixed_minimum() -> None:
    steady = phase(300, 100, 110, completed=301, requests=301)
    spike = phase(750, 200, 220, completed=751, requests=751)
    recovery = phase(300, 120, 130, completed=301, requests=301)

    verdict = compare_benchmarks(
        measurement("before", steady=steady, spike=spike, recovery=recovery),
        measurement("after", steady=steady, spike=spike, recovery=recovery),
    )

    assert verdict.status == "pass"
    assert check_status(verdict, "steady_offered_load") == "pass"


def test_cost_increase_warns_without_failing_safety() -> None:
    verdict = compare_benchmarks(
        measurement("before", request_cost_usd=Decimal("10")),
        measurement("after", request_cost_usd=Decimal("12")),
    )

    assert verdict.status == "pass"
    assert check_status(verdict, "cost_not_reduced") == "warning"
    assert verdict.cost_change_percent == Decimal("20.000")


def test_k6_summary_contract_is_typed() -> None:
    summary = K6RunSummary.model_validate(
        {
            "schema_version": 1,
            "profile_version": "kubefit-load-v1",
            "proposal_id": PROPOSAL_ID,
            "variant": "before",
            "dropped_iterations": 0,
            "steady": phase(300, 100, 110),
            "spike": phase(750, 200, 220),
            "recovery": phase(300, 120, 130),
        }
    )

    assert summary.steady.completed_iterations == 300


def test_rejects_inverted_latency_percentiles() -> None:
    with pytest.raises(ValidationError, match="P99"):
        K6RunSummary.model_validate(
            {
                "schema_version": 1,
                "profile_version": "kubefit-load-v1",
                "proposal_id": PROPOSAL_ID,
                "variant": "before",
                "dropped_iterations": 0,
                "steady": phase(300, 110, 100),
                "spike": phase(750, 200, 220),
                "recovery": phase(300, 120, 130),
            }
        )


def runtime(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "cpu_throttling_p95_percent": 1,
        "oom_killed_count": 0,
        "restart_count": 0,
        "traffic_spike_recovery_seconds": 10,
    }
    values.update(overrides)
    return values


def provenance() -> dict[str, object]:
    started_at = datetime(2026, 8, 21, tzinfo=UTC)
    return {
        "run_started_at": started_at,
        "run_finished_at": started_at + timedelta(seconds=160),
        "pods": ["api-abc"],
        "k6_summary_sha256": "a" * 64,
        "k6_raw_sha256": "b" * 64,
        "prometheus_rate_window_seconds": 30,
    }
