from datetime import timedelta
from pathlib import Path

from benchmarks import (
    BenchmarkRun,
    assess_counterbalanced_pair,
    compare_benchmarks,
    execute_benchmark,
    write_benchmark_result,
)
from tests.test_benchmark_runner import RecordingController, collector, published_proposal


def published_pair(tmp_path: Path):
    proposal = published_proposal(tmp_path)
    artifacts = []
    for order in ("before-after", "after-before"):
        controller = RecordingController()
        run = execute_benchmark(
            proposal.path,
            controller,
            collector(controller.events),
            execution_order=order,
        )
        artifacts.append(write_benchmark_result(tmp_path / "results", run))
    return proposal, artifacts


def test_passes_two_opposite_order_artifacts_deterministically(tmp_path: Path) -> None:
    proposal, artifacts = published_pair(tmp_path)

    assessment = assess_counterbalanced_pair(artifacts[0].path, artifacts[1].path)
    reversed_assessment = assess_counterbalanced_pair(
        artifacts[1].path, artifacts[0].path
    )

    assert assessment == reversed_assessment
    assert assessment.assessment_id.startswith("benchmark-pair-")
    assert assessment.proposal_id == proposal.artifact_id
    assert assessment.status == "pass"
    assert {trial.measurement_order for trial in assessment.trials} == {
        "before-after",
        "after-before",
    }
    assert {check.status for check in assessment.checks} == {"pass"}
    assert assessment.failures == []
    assert assessment.invalid_reasons == []
    assert "do not estimate run-to-run variance" in assessment.warnings[0]


def test_invalidates_duplicate_artifact_instead_of_claiming_a_pair(tmp_path: Path) -> None:
    _, artifacts = published_pair(tmp_path)

    assessment = assess_counterbalanced_pair(artifacts[0].path, artifacts[0].path)

    assert assessment.status == "invalid"
    assert {check.code for check in assessment.checks if check.status == "invalid"} == {
        "distinct_artifacts",
        "opposite_orders",
    }


def test_invalidates_two_distinct_artifacts_with_the_same_order(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    controller = RecordingController()
    run = execute_benchmark(proposal.path, controller, collector(controller.events))
    first = write_benchmark_result(tmp_path / "results", run)
    shifted = _shift_run(run, timedelta(hours=1))
    second = write_benchmark_result(tmp_path / "results", shifted)

    assessment = assess_counterbalanced_pair(first.path, second.path)

    assert first.artifact_id != second.artifact_id
    assert assessment.status == "invalid"
    assert next(
        check.status for check in assessment.checks if check.code == "opposite_orders"
    ) == "invalid"


def test_fails_when_opposite_orders_disagree_on_candidate_safety(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    before_first_controller = RecordingController()
    before_first = execute_benchmark(
        proposal.path,
        before_first_controller,
        collector(before_first_controller.events),
        execution_order="before-after",
    )
    after_first_controller = RecordingController()
    after_first = execute_benchmark(
        proposal.path,
        after_first_controller,
        collector(after_first_controller.events),
        execution_order="after-before",
    )
    failed_after_first = _with_candidate_oom(after_first)
    first = write_benchmark_result(tmp_path / "results", before_first)
    second = write_benchmark_result(tmp_path / "results", failed_after_first)

    assessment = assess_counterbalanced_pair(first.path, second.path)

    assert assessment.status == "fail"
    assert {check.code for check in assessment.checks if check.status == "fail"} == {
        "policy_check_agreement",
        "both_trials_pass",
    }
    assert len(assessment.failures) == 2
    assert assessment.invalid_reasons == []


def _shift_run(run: BenchmarkRun, delta: timedelta) -> BenchmarkRun:
    before = run.before.model_copy(
        update={"provenance": _shift_provenance(run.before.provenance, delta)}
    )
    after = run.after.model_copy(
        update={"provenance": _shift_provenance(run.after.provenance, delta)}
    )
    return BenchmarkRun(
        **run.model_dump(exclude={"before", "after", "verdict"}),
        before=before,
        after=after,
        verdict=compare_benchmarks(before, after),
    )


def _shift_provenance(provenance, delta: timedelta):
    return provenance.model_copy(
        update={
            "run_started_at": provenance.run_started_at + delta,
            "run_finished_at": provenance.run_finished_at + delta,
        }
    )


def _with_candidate_oom(run: BenchmarkRun) -> BenchmarkRun:
    after = run.after.model_copy(
        update={
            "runtime": run.after.runtime.model_copy(update={"oom_killed_count": 1})
        }
    )
    return BenchmarkRun(
        **run.model_dump(exclude={"after", "verdict"}),
        after=after,
        verdict=compare_benchmarks(run.before, after),
    )
