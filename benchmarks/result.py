from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PROFILE_VERSION = "kubefit-load-v1"
EXPECTED_ITERATIONS = {"steady": 300, "spike": 750, "recovery": 300}
MAX_SCHEDULER_BOUNDARY_OVERSHOOT = 1


class LoadPhaseMetrics(BaseModel):
    expected_iterations: int = Field(gt=0)
    completed_iterations: int = Field(ge=0)
    requests: int = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    latency_p95_ms: float = Field(ge=0)
    latency_p99_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def percentiles_are_ordered(self) -> "LoadPhaseMetrics":
        if self.latency_p99_ms < self.latency_p95_ms:
            raise ValueError("latency P99 must be greater than or equal to P95")
        return self


class K6RunSummary(BaseModel):
    schema_version: Literal[1] = 1
    profile_version: str = Field(min_length=1)
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    variant: Literal["before", "after"]
    dropped_iterations: int = Field(ge=0)
    steady: LoadPhaseMetrics
    spike: LoadPhaseMetrics
    recovery: LoadPhaseMetrics


class RuntimeBenchmarkSignals(BaseModel):
    cpu_throttling_p95_percent: float = Field(ge=0, le=100)
    oom_killed_count: int = Field(ge=0)
    restart_count: int = Field(ge=0)
    traffic_spike_recovery_seconds: float = Field(ge=0)
    traffic_spike_recovered: bool = True


class MeasurementProvenance(BaseModel):
    run_started_at: datetime
    run_finished_at: datetime
    pods: list[str] = Field(min_length=1)
    k6_summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    k6_raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prometheus_rate_window_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def evidence_is_ordered(self) -> "MeasurementProvenance":
        if self.run_started_at.tzinfo is None or self.run_finished_at.tzinfo is None:
            raise ValueError("measurement timestamps must include timezone information")
        if self.run_finished_at <= self.run_started_at:
            raise ValueError("measurement finish must be later than start")
        if self.pods != sorted(set(self.pods)):
            raise ValueError("measurement Pods must be sorted and unique")
        return self


class BenchmarkMeasurement(K6RunSummary):
    runtime: RuntimeBenchmarkSignals
    provenance: MeasurementProvenance
    request_cost_usd: Decimal = Field(gt=0)


class BenchmarkPolicy(BaseModel):
    steady_latency_regression_percent: Decimal = Field(default=Decimal("10"), ge=0)
    spike_latency_regression_percent: Decimal = Field(default=Decimal("15"), ge=0)
    error_rate_after: Decimal = Field(default=Decimal("0.01"), ge=0, le=1)
    error_rate_increase: Decimal = Field(default=Decimal("0.005"), ge=0, le=1)
    throttling_after_percent: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    throttling_increase_percentage_points: Decimal = Field(default=Decimal("1"), ge=0, le=100)
    recovery_regression_percent: Decimal = Field(default=Decimal("20"), ge=0)


class BenchmarkCheck(BaseModel):
    code: str
    status: Literal["pass", "fail", "invalid", "warning"]
    reason: str


class BenchmarkVerdict(BaseModel):
    status: Literal["pass", "fail", "invalid"]
    checks: list[BenchmarkCheck]
    failures: list[str]
    invalid_reasons: list[str]
    warnings: list[str]
    cost_change_percent: Decimal | None


def measurement_order(
    before: BenchmarkMeasurement, after: BenchmarkMeasurement
) -> Literal["before-after", "after-before"] | None:
    if before.provenance.run_finished_at <= after.provenance.run_started_at:
        return "before-after"
    if after.provenance.run_finished_at <= before.provenance.run_started_at:
        return "after-before"
    return None


def compare_benchmarks(
    before: BenchmarkMeasurement,
    after: BenchmarkMeasurement,
    policy: BenchmarkPolicy | None = None,
) -> BenchmarkVerdict:
    policy = policy or BenchmarkPolicy()
    checks = _validity_checks(before, after)
    invalid_reasons = [check.reason for check in checks if check.status == "invalid"]
    if invalid_reasons:
        return BenchmarkVerdict(
            status="invalid",
            checks=checks,
            failures=[],
            invalid_reasons=invalid_reasons,
            warnings=[],
            cost_change_percent=_cost_change(before, after),
        )

    order = measurement_order(before, after)
    assert order is not None
    first, second = order.split("-")
    labels = {"before": "baseline (before)", "after": "candidate (after)"}
    checks.append(
        BenchmarkCheck(
            code="measurement_order_bias",
            status="warning",
            reason=(
                f"{labels[first]} was measured before {labels[second]}; one sequential "
                "trial cannot "
                "separate resource effects from warm-up or time drift, so run the "
                "opposite execution order as a counterbalanced trial"
            ),
        )
    )

    for phase, limit in (
        ("steady", policy.steady_latency_regression_percent),
        ("spike", policy.spike_latency_regression_percent),
    ):
        baseline = getattr(before, phase)
        candidate = getattr(after, phase)
        for percentile in ("p95", "p99"):
            before_value = getattr(baseline, f"latency_{percentile}_ms")
            after_value = getattr(candidate, f"latency_{percentile}_ms")
            checks.append(
                _regression_check(
                    code=f"{phase}_latency_{percentile}",
                    label=f"{phase} latency {percentile.upper()}",
                    before=before_value,
                    after=after_value,
                    allowed_percent=limit,
                )
            )

    for phase in EXPECTED_ITERATIONS:
        before_error = Decimal(str(getattr(before, phase).error_rate))
        after_error = Decimal(str(getattr(after, phase).error_rate))
        checks.append(
            _maximum_check(
                f"{phase}_error_rate_after",
                f"candidate {phase} error rate",
                after_error,
                policy.error_rate_after,
                suffix="",
            )
        )
        checks.append(
            _maximum_check(
                f"{phase}_error_rate_increase",
                f"{phase} error-rate increase",
                after_error - before_error,
                policy.error_rate_increase,
                suffix=" percentage points",
            )
        )

    before_throttling = Decimal(str(before.runtime.cpu_throttling_p95_percent))
    after_throttling = Decimal(str(after.runtime.cpu_throttling_p95_percent))
    checks.append(
        _maximum_check(
            "throttling_after",
            "candidate CPU throttling P95",
            after_throttling,
            policy.throttling_after_percent,
            suffix="%",
        )
    )
    checks.append(
        _maximum_check(
            "throttling_increase",
            "CPU throttling P95 increase",
            after_throttling - before_throttling,
            policy.throttling_increase_percentage_points,
            suffix=" percentage points",
        )
    )

    candidate_ooms = after.runtime.oom_killed_count
    checks.append(
        BenchmarkCheck(
            code="new_oom_killed",
            status="fail" if candidate_ooms > 0 else "pass",
            reason=(
                f"candidate run observed {candidate_ooms} OOMKilled event(s) "
                f"(baseline: {before.runtime.oom_killed_count})"
                if candidate_ooms > 0
                else "candidate run observed no OOMKilled events"
            ),
        )
    )
    checks.append(
        BenchmarkCheck(
            code="traffic_spike_recovered",
            status="pass" if after.runtime.traffic_spike_recovered else "fail",
            reason=(
                "candidate recovered during the fixed recovery phase"
                if after.runtime.traffic_spike_recovered
                else "candidate did not recover during the fixed recovery phase"
            ),
        )
    )
    checks.append(
        _regression_check(
            code="traffic_spike_recovery",
            label="traffic-spike recovery time",
            before=before.runtime.traffic_spike_recovery_seconds,
            after=after.runtime.traffic_spike_recovery_seconds,
            allowed_percent=policy.recovery_regression_percent,
        )
    )

    if after.runtime.restart_count > before.runtime.restart_count:
        checks.append(
            BenchmarkCheck(
                code="new_restarts",
                status="warning",
                reason=(
                    "candidate restart count increased from "
                    f"{before.runtime.restart_count} to {after.runtime.restart_count}; "
                    "inspect rollout and container termination evidence"
                ),
            )
        )

    cost_change = _cost_change(before, after)
    if cost_change is not None and cost_change >= 0:
        checks.append(
            BenchmarkCheck(
                code="cost_not_reduced",
                status="warning",
                reason=f"request cost changed by {cost_change}% instead of decreasing",
            )
        )

    failures = [check.reason for check in checks if check.status == "fail"]
    warnings = [check.reason for check in checks if check.status == "warning"]
    return BenchmarkVerdict(
        status="fail" if failures else "pass",
        checks=checks,
        failures=failures,
        invalid_reasons=[],
        warnings=warnings,
        cost_change_percent=cost_change,
    )


def _validity_checks(
    before: BenchmarkMeasurement, after: BenchmarkMeasurement
) -> list[BenchmarkCheck]:
    checks = []
    comparisons = (
        (
            "variant_pair",
            before.variant == "before" and after.variant == "after",
            "results must be ordered as before then after",
        ),
        (
            "proposal_id_match",
            before.proposal_id == after.proposal_id,
            "before and after proposal IDs must match",
        ),
        (
            "profile_version_match",
            before.profile_version == after.profile_version == PROFILE_VERSION,
            f"both results must use {PROFILE_VERSION}",
        ),
        (
            "dropped_iterations",
            before.dropped_iterations == after.dropped_iterations == 0,
            "before and after runs must not drop offered iterations",
        ),
        (
            "baseline_recovered",
            before.runtime.traffic_spike_recovered,
            "baseline must recover during the fixed recovery phase",
        ),
        (
            "measurement_intervals",
            measurement_order(before, after) is not None,
            "before and after measurement intervals must not overlap",
        ),
    )
    for code, valid, failure_reason in comparisons:
        checks.append(
            BenchmarkCheck(
                code=code,
                status="pass" if valid else "invalid",
                reason=f"{code} is valid" if valid else failure_reason,
            )
        )

    for phase, expected in EXPECTED_ITERATIONS.items():
        before_phase = getattr(before, phase)
        after_phase = getattr(after, phase)
        valid = (
            before_phase.expected_iterations == after_phase.expected_iterations == expected
            and expected
            <= before_phase.completed_iterations
            <= expected + MAX_SCHEDULER_BOUNDARY_OVERSHOOT
            and expected
            <= after_phase.completed_iterations
            <= expected + MAX_SCHEDULER_BOUNDARY_OVERSHOOT
            and before_phase.requests >= before_phase.completed_iterations
            and after_phase.requests >= after_phase.completed_iterations
        )
        if (
            valid
            and before_phase.completed_iterations == expected
            and after_phase.completed_iterations == expected
        ):
            reason = f"{phase} completed the fixed {expected}-iteration load"
        elif valid:
            reason = (
                f"{phase} completed the fixed {expected}-iteration minimum within "
                "the one-iteration scheduler boundary allowance "
                f"(before: {before_phase.completed_iterations}, "
                f"after: {after_phase.completed_iterations})"
            )
        else:
            reason = (
                f"{phase} must complete {expected} to "
                f"{expected + MAX_SCHEDULER_BOUNDARY_OVERSHOOT} iterations in both runs"
            )
        checks.append(
            BenchmarkCheck(
                code=f"{phase}_offered_load",
                status="pass" if valid else "invalid",
                reason=reason,
            )
        )
    return checks


def _regression_check(
    code: str,
    label: str,
    before: float,
    after: float,
    allowed_percent: Decimal,
) -> BenchmarkCheck:
    regression = _percent_change(before, after)
    failed = regression is None or regression > allowed_percent
    if regression is None:
        reason = f"{label} rose from a zero baseline to {after}"
    else:
        displayed = regression.quantize(Decimal("0.001"))
        reason = f"{label} changed by {displayed}% (allowed regression: {allowed_percent}%)"
    return BenchmarkCheck(code=code, status="fail" if failed else "pass", reason=reason)


def _maximum_check(
    code: str,
    label: str,
    value: Decimal,
    maximum: Decimal,
    suffix: str,
) -> BenchmarkCheck:
    failed = value > maximum
    return BenchmarkCheck(
        code=code,
        status="fail" if failed else "pass",
        reason=f"{label} is {value}{suffix} (maximum: {maximum}{suffix})",
    )


def _percent_change(before: float, after: float) -> Decimal | None:
    baseline = Decimal(str(before))
    candidate = Decimal(str(after))
    if baseline == 0:
        return Decimal("0") if candidate == 0 else None
    return (candidate - baseline) / baseline * Decimal("100")


def _cost_change(before: BenchmarkMeasurement, after: BenchmarkMeasurement) -> Decimal | None:
    baseline = before.request_cost_usd
    if baseline == 0:
        return None
    return ((after.request_cost_usd - baseline) / baseline * Decimal("100")).quantize(
        Decimal("0.001")
    )
