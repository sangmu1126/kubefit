from pathlib import Path
from types import SimpleNamespace

from benchmarks import review_counterbalanced_pair, write_counterbalanced_pair
from benchmarks.pair_review import _metric_comparison, _metric_trial
from tests.test_benchmark import measurement
from tests.test_benchmark_pair import published_pair


def test_replays_pair_and_projects_order_aware_metric_ranges(tmp_path: Path) -> None:
    proposal, trials = published_pair(tmp_path)
    pair = write_counterbalanced_pair(
        tmp_path / "pairs", trials[0].path, trials[1].path
    )

    review = review_counterbalanced_pair(pair.path)

    assert review.artifact_id == pair.artifact_id
    assert review.proposal_id == proposal.artifact_id
    assert review.status == "pass"
    assert review.verification_level == "pair_full_artifact_replay"
    assert review.benchmark_ids == pair.benchmark_ids
    assert len(review.metrics) == 6
    assert {trial.measurement_order for trial in review.metrics[0].trials} == {
        "before-after",
        "after-before",
    }
    assert {metric.direction for metric in review.metrics} == {"unchanged"}
    assert "not a confidence interval" in review.limitations[0]
    assert "do not estimate run-to-run variance" in review.limitations[1]


def test_metric_projection_reports_direction_percent_and_zero_baseline() -> None:
    before = measurement("before")
    improved = measurement(
        "after",
        steady={
            **before.steady.model_dump(),
            "latency_p95_ms": 80,
        },
    )

    latency = _metric_trial(
        "benchmark-" + "a" * 32,
        "before-after",
        before,
        improved,
        lambda item: item.steady.latency_p95_ms,
    )
    assert latency.delta == -20
    assert latency.change_percent == -20
    assert latency.direction == "improved"

    zero_before = before.model_copy(
        update={
            "runtime": before.runtime.model_copy(
                update={"cpu_throttling_p95_percent": 0}
            )
        }
    )
    throttled = improved.model_copy(
        update={
            "runtime": improved.runtime.model_copy(
                update={"cpu_throttling_p95_percent": 0.5}
            )
        }
    )
    zero_baseline = _metric_trial(
        "benchmark-" + "b" * 32,
        "after-before",
        zero_before,
        throttled,
        lambda item: item.runtime.cpu_throttling_p95_percent,
    )
    assert zero_baseline.delta == 0.5
    assert zero_baseline.change_percent is None
    assert zero_baseline.direction == "regressed"


def test_metric_projection_marks_opposite_directions_as_mixed() -> None:
    baseline = measurement("before")
    improved = measurement(
        "after",
        steady={**baseline.steady.model_dump(), "latency_p95_ms": 90},
    )
    regressed = measurement(
        "after",
        steady={**baseline.steady.model_dump(), "latency_p95_ms": 105},
    )

    comparison = _metric_comparison(
        "steady_latency_p95",
        "Steady latency P95",
        "ms",
        lambda item: item.steady.latency_p95_ms,
        [
            (
                "before-after",
                SimpleNamespace(
                    artifact_id="benchmark-" + "a" * 32,
                    before=baseline,
                    after=improved,
                ),
            ),
            (
                "after-before",
                SimpleNamespace(
                    artifact_id="benchmark-" + "b" * 32,
                    before=baseline,
                    after=regressed,
                ),
            ),
        ],
    )

    assert comparison.direction == "mixed"
    assert comparison.delta_min == -10
    assert comparison.delta_max == 5
    assert comparison.change_percent_min == -10
    assert comparison.change_percent_max == 5
