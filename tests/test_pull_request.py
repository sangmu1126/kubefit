from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarks import compare_benchmarks, write_benchmark_result
from evaluator import AnalysisArtifact, AnalysisTarget
from gitops import write_proposal_bundle
from gitops.pull_request import PullRequestPlanError, build_pull_request_plan
from tests.test_benchmark_artifact import completed_run
from tests.test_bundle import proposal_inputs
from tests.test_manifest import FIXTURES

EXPECTED_BODY = Path(__file__).parent / "fixtures/pull_request/expected.md"


def published_pair(tmp_path: Path):
    proposal, run = completed_run(tmp_path)
    benchmark = write_benchmark_result(tmp_path / "results", run)
    return proposal, benchmark, run


def test_builds_golden_single_file_draft_plan(tmp_path: Path) -> None:
    proposal, benchmark, _ = published_pair(tmp_path)

    plan = build_pull_request_plan(proposal.path, benchmark.path)

    assert plan.draft is True
    assert plan.branch_name == "kubefit/demo-demo-444bf372"
    assert plan.title == "KubeFit: optimize demo/demo resources"
    assert plan.commit_message == "kubefit: optimize demo/demo resources"
    assert plan.proposal_id == proposal.artifact_id
    assert plan.benchmark_id == benchmark.artifact_id
    assert plan.file_change.path == "deploy/demo.yaml"
    assert plan.file_change.before_content == (FIXTURES / "input.yaml").read_text()
    assert plan.file_change.after_content == (FIXTURES / "expected.yaml").read_text()
    assert len(plan.resource_changes) == 4
    assert plan.body == EXPECTED_BODY.read_text()


def test_rejects_non_passing_benchmark(tmp_path: Path) -> None:
    proposal, _, run = published_pair(tmp_path)
    run.after.runtime = run.after.runtime.model_copy(
        update={"oom_killed_count": 1}
    )
    run.verdict = compare_benchmarks(run.before, run.after)
    assert run.verdict.status == "fail"
    failed = write_benchmark_result(tmp_path / "failed", run)

    with pytest.raises(PullRequestPlanError, match="must be pass"):
        build_pull_request_plan(proposal.path, failed.path)


def test_rejects_benchmark_cost_that_conflicts_with_proposal(tmp_path: Path) -> None:
    proposal, _, run = published_pair(tmp_path)
    run.after.request_cost_usd += 1
    run.verdict = compare_benchmarks(run.before, run.after)
    mismatched = write_benchmark_result(tmp_path / "mismatched", run)

    with pytest.raises(PullRequestPlanError, match="candidate benchmark cost conflicts"):
        build_pull_request_plan(proposal.path, mismatched.path)


def test_rejects_benchmark_bound_to_another_proposal(tmp_path: Path) -> None:
    _, benchmark, _ = published_pair(tmp_path)
    _, evaluation, patch = proposal_inputs()
    analysis = AnalysisArtifact(
        target=AnalysisTarget(**patch.report.target.model_dump()),
        workload_uid="another-deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=evaluation,
    )
    other = write_proposal_bundle(
        tmp_path / "other-proposals",
        patch,
        evaluation,
        analysis=analysis,
    )

    with pytest.raises(PullRequestPlanError, match="does not reference"):
        build_pull_request_plan(other.path, benchmark.path)
