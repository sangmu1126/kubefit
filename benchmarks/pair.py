import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.artifact import BenchmarkResultArtifactError, LoadedBenchmarkResult
from benchmarks.result import measurement_order


class CounterbalancedPairError(RuntimeError):
    """Raised when benchmark artifacts cannot be loaded for pair assessment."""


class CounterbalancedTrial(BaseModel):
    model_config = ConfigDict(frozen=True)

    benchmark_id: str = Field(pattern=r"^benchmark-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    measurement_order: Literal["before-after", "after-before"] | None
    verdict_status: Literal["pass", "fail", "invalid"]
    policy_check_statuses: dict[str, Literal["pass", "fail", "invalid", "warning"]]


class CounterbalancedPairCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    status: Literal["pass", "fail", "invalid", "warning"]
    reason: str


class CounterbalancedPairAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    assessment_id: str = Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")
    proposal_id: str | None = Field(
        default=None, pattern=r"^proposal-[0-9a-f]{32}$"
    )
    status: Literal["pass", "fail", "invalid"]
    trials: list[CounterbalancedTrial] = Field(min_length=2, max_length=2)
    checks: list[CounterbalancedPairCheck]
    failures: list[str]
    invalid_reasons: list[str]
    warnings: list[str]


def assess_counterbalanced_pair(
    first_path: Path,
    second_path: Path,
) -> CounterbalancedPairAssessment:
    from benchmarks.artifact import load_benchmark_result

    try:
        loaded = [load_benchmark_result(first_path), load_benchmark_result(second_path)]
    except (BenchmarkResultArtifactError, OSError) as exc:
        raise CounterbalancedPairError(
            "counterbalanced assessment requires two valid benchmark artifacts"
        ) from exc
    return _assess_loaded_pair(*loaded)


def _assess_loaded_pair(
    first: LoadedBenchmarkResult,
    second: LoadedBenchmarkResult,
) -> CounterbalancedPairAssessment:
    results = sorted((first, second), key=lambda result: result.artifact_id)
    trials = [_trial(result) for result in results]
    checks = _input_checks(results, trials)
    invalid_reasons = [check.reason for check in checks if check.status == "invalid"]
    proposal_ids = sorted({trial.proposal_id for trial in trials})
    proposal_id = proposal_ids[0] if len(proposal_ids) == 1 else None
    warnings = [
        (
            "two opposite-order trials reduce directional order bias but do not "
            "estimate run-to-run variance or establish statistical significance"
        )
    ]

    if invalid_reasons:
        return _assessment(
            proposal_id=proposal_id,
            status="invalid",
            trials=trials,
            checks=checks,
            failures=[],
            invalid_reasons=invalid_reasons,
            warnings=warnings,
        )

    policy_agrees = trials[0].policy_check_statuses == trials[1].policy_check_statuses
    checks.append(
        CounterbalancedPairCheck(
            code="policy_check_agreement",
            status="pass" if policy_agrees else "fail",
            reason=(
                "both orders produced identical non-order policy check statuses"
                if policy_agrees
                else "opposite orders produced different non-order policy check statuses"
            ),
        )
    )
    both_pass = all(trial.verdict_status == "pass" for trial in trials)
    checks.append(
        CounterbalancedPairCheck(
            code="both_trials_pass",
            status="pass" if both_pass else "fail",
            reason=(
                "both opposite-order benchmark verdicts passed"
                if both_pass
                else "both opposite-order benchmark verdicts must pass"
            ),
        )
    )
    failures = [check.reason for check in checks if check.status == "fail"]
    return _assessment(
        proposal_id=proposal_id,
        status="fail" if failures else "pass",
        trials=trials,
        checks=checks,
        failures=failures,
        invalid_reasons=[],
        warnings=warnings,
    )


def _trial(result: LoadedBenchmarkResult) -> CounterbalancedTrial:
    return CounterbalancedTrial(
        benchmark_id=result.artifact_id,
        proposal_id=result.proposal_id,
        measurement_order=measurement_order(result.before, result.after),
        verdict_status=result.verdict.status,
        policy_check_statuses={
            check.code: check.status
            for check in result.verdict.checks
            if check.code != "measurement_order_bias"
        },
    )


def _input_checks(
    results: list[LoadedBenchmarkResult],
    trials: list[CounterbalancedTrial],
) -> list[CounterbalancedPairCheck]:
    distinct = trials[0].benchmark_id != trials[1].benchmark_id
    same_proposal = trials[0].proposal_id == trials[1].proposal_id
    orders = {trial.measurement_order for trial in trials}
    opposite_orders = orders == {"before-after", "after-before"}
    same_profile = all(
        result.before.profile_version == results[0].before.profile_version
        and result.after.profile_version == results[0].after.profile_version
        for result in results
    )
    same_cost_basis = (
        results[0].before.request_cost_usd == results[1].before.request_cost_usd
        and results[0].after.request_cost_usd == results[1].after.request_cost_usd
    )
    values = (
        (
            "distinct_artifacts",
            distinct,
            "counterbalanced trials must be distinct benchmark artifacts",
        ),
        (
            "proposal_binding",
            same_proposal,
            "counterbalanced trials must reference the same proposal",
        ),
        (
            "opposite_orders",
            opposite_orders,
            "counterbalanced trials must contain exactly one run in each execution order",
        ),
        (
            "profile_binding",
            same_profile,
            "counterbalanced trials must use the same load profile version",
        ),
        (
            "cost_basis_binding",
            same_cost_basis,
            "counterbalanced trials must use identical before and after request costs",
        ),
    )
    return [
        CounterbalancedPairCheck(
            code=code,
            status="pass" if valid else "invalid",
            reason=f"{code} is valid" if valid else invalid_reason,
        )
        for code, valid, invalid_reason in values
    ]


def _assessment(
    *,
    proposal_id: str | None,
    status: Literal["pass", "fail", "invalid"],
    trials: list[CounterbalancedTrial],
    checks: list[CounterbalancedPairCheck],
    failures: list[str],
    invalid_reasons: list[str],
    warnings: list[str],
) -> CounterbalancedPairAssessment:
    content = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "status": status,
        "trials": [trial.model_dump(mode="json") for trial in trials],
        "checks": [check.model_dump(mode="json") for check in checks],
        "failures": failures,
        "invalid_reasons": invalid_reasons,
        "warnings": warnings,
    }
    digest = hashlib.sha256(_canonical_json(content)).hexdigest()
    return CounterbalancedPairAssessment(
        assessment_id=f"benchmark-pair-{digest[:32]}",
        **content,
    )


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
