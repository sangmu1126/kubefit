from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.pair import CounterbalancedPairCheck
from benchmarks.pair_artifact import LoadedCounterbalancedPair, load_counterbalanced_pair
from benchmarks.result import BenchmarkMeasurement


class PairMetricTrial(BaseModel):
    model_config = ConfigDict(frozen=True)

    benchmark_id: str = Field(pattern=r"^benchmark-[0-9a-f]{32}$")
    measurement_order: Literal["before-after", "after-before"]
    before: float = Field(ge=0)
    after: float = Field(ge=0)
    delta: float
    change_percent: float | None
    direction: Literal["improved", "regressed", "unchanged"]


class PairMetricComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: Literal[
        "steady_latency_p95",
        "steady_latency_p99",
        "spike_latency_p95",
        "spike_latency_p99",
        "cpu_throttling_p95",
        "traffic_spike_recovery",
    ]
    label: str
    unit: Literal["ms", "%", "s"]
    lower_is_better: Literal[True] = True
    direction: Literal["improved", "regressed", "unchanged", "mixed"]
    delta_min: float
    delta_max: float
    change_percent_min: float | None
    change_percent_max: float | None
    trials: list[PairMetricTrial] = Field(min_length=2, max_length=2)


class CounterbalancedPairReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    verification_level: Literal["pair_full_artifact_replay"] = (
        "pair_full_artifact_replay"
    )
    status: Literal["pass"] = "pass"
    benchmark_ids: list[str] = Field(min_length=2, max_length=2)
    metrics: list[PairMetricComparison]
    checks: list[CounterbalancedPairCheck]
    limitations: list[str]


def review_counterbalanced_pair(path: Path) -> CounterbalancedPairReview:
    """Replay a complete pair and project order-aware metric observations."""
    return review_loaded_counterbalanced_pair(load_counterbalanced_pair(path))


def review_loaded_counterbalanced_pair(
    loaded: LoadedCounterbalancedPair,
) -> CounterbalancedPairReview:
    """Project an already verified pair without loading its evidence twice."""
    trials = [
        ("before-after", loaded.before_after),
        ("after-before", loaded.after_before),
    ]
    metric_specs = (
        (
            "steady_latency_p95",
            "Steady latency P95",
            "ms",
            lambda measurement: measurement.steady.latency_p95_ms,
        ),
        (
            "steady_latency_p99",
            "Steady latency P99",
            "ms",
            lambda measurement: measurement.steady.latency_p99_ms,
        ),
        (
            "spike_latency_p95",
            "Spike latency P95",
            "ms",
            lambda measurement: measurement.spike.latency_p95_ms,
        ),
        (
            "spike_latency_p99",
            "Spike latency P99",
            "ms",
            lambda measurement: measurement.spike.latency_p99_ms,
        ),
        (
            "cpu_throttling_p95",
            "CPU throttling P95",
            "%",
            lambda measurement: measurement.runtime.cpu_throttling_p95_percent,
        ),
        (
            "traffic_spike_recovery",
            "Traffic-spike recovery",
            "s",
            lambda measurement: measurement.runtime.traffic_spike_recovery_seconds,
        ),
    )
    metrics = [
        _metric_comparison(code, label, unit, extractor, trials)
        for code, label, unit, extractor in metric_specs
    ]
    return CounterbalancedPairReview(
        artifact_id=loaded.artifact_id,
        proposal_id=loaded.proposal_id,
        benchmark_ids=sorted(
            [loaded.before_after.artifact_id, loaded.after_before.artifact_id]
        ),
        metrics=metrics,
        checks=loaded.assessment.checks,
        limitations=[
            (
                "the displayed range is the minimum and maximum of two observed "
                "order-specific changes, not a confidence interval"
            ),
            (
                "two trials reduce directional order bias but do not estimate "
                "run-to-run variance or establish statistical significance"
            ),
            (
                "the fixed benchmark profile does not establish representative "
                "production traffic"
            ),
        ],
    )


def _metric_comparison(code, label, unit, extractor, results) -> PairMetricComparison:
    trials = [
        _metric_trial(result.artifact_id, order, result.before, result.after, extractor)
        for order, result in results
    ]
    deltas = [trial.delta for trial in trials]
    percentages = [
        trial.change_percent for trial in trials if trial.change_percent is not None
    ]
    directions = {trial.direction for trial in trials}
    direction = directions.pop() if len(directions) == 1 else "mixed"
    return PairMetricComparison(
        code=code,
        label=label,
        unit=unit,
        direction=direction,
        delta_min=min(deltas),
        delta_max=max(deltas),
        change_percent_min=min(percentages) if len(percentages) == 2 else None,
        change_percent_max=max(percentages) if len(percentages) == 2 else None,
        trials=trials,
    )


def _metric_trial(
    benchmark_id: str,
    order: Literal["before-after", "after-before"],
    before: BenchmarkMeasurement,
    after: BenchmarkMeasurement,
    extractor,
) -> PairMetricTrial:
    before_value = float(extractor(before))
    after_value = float(extractor(after))
    delta = _rounded(after_value - before_value)
    change_percent = (
        _rounded(delta / before_value * 100) if before_value != 0 else None
    )
    return PairMetricTrial(
        benchmark_id=benchmark_id,
        measurement_order=order,
        before=_rounded(before_value),
        after=_rounded(after_value),
        delta=delta,
        change_percent=change_percent,
        direction="improved" if delta < 0 else "regressed" if delta > 0 else "unchanged",
    )


def _rounded(value: float) -> float:
    rounded = round(value, 6)
    return 0.0 if rounded == 0 else rounded
