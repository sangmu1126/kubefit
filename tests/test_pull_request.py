from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarks import (
    compare_benchmarks,
    load_counterbalanced_pair,
    write_benchmark_campaign_evidence,
    write_benchmark_result,
)
from evaluator import AnalysisArtifact, AnalysisTarget
from gitops import write_proposal_bundle
from gitops.pull_request import PullRequestPlanError, build_pull_request_plan
from tests.test_benchmark_artifact import completed_run
from tests.test_benchmark_campaign_artifact import completed_campaign
from tests.test_benchmark_pair_artifact import publication_artifacts
from tests.test_bundle import proposal_inputs
from tests.test_manifest import FIXTURES

EXPECTED_BODY = Path(__file__).parent / "fixtures/pull_request/expected.md"


def published_pair(tmp_path: Path):
    proposal, run = completed_run(tmp_path)
    benchmark = write_benchmark_result(tmp_path / "results", run)
    return proposal, benchmark, run


def test_builds_golden_single_file_draft_plan(tmp_path: Path) -> None:
    proposal, benchmark, pair = publication_artifacts(tmp_path)

    plan = build_pull_request_plan(proposal.path, benchmark.path, pair.path)

    assert plan.draft is True
    assert plan.branch_name == "kubefit/demo-demo-444bf372"
    assert plan.title == "KubeFit: optimize demo/demo resources"
    assert plan.commit_message == "kubefit: optimize demo/demo resources"
    assert plan.proposal_id == proposal.artifact_id
    assert plan.benchmark_id == benchmark.artifact_id
    assert plan.benchmark_pair_id == pair.artifact_id
    assert plan.benchmark_ids == pair.benchmark_ids
    assert plan.file_change.path == "deploy/demo.yaml"
    assert plan.file_change.before_content == (FIXTURES / "input.yaml").read_text()
    assert plan.file_change.after_content == (FIXTURES / "expected.yaml").read_text()
    assert len(plan.resource_changes) == 4
    assert plan.body == EXPECTED_BODY.read_text()


def test_attaches_only_explicit_completed_campaign_evidence(tmp_path: Path) -> None:
    proposal, campaign, pair_paths = completed_campaign(
        tmp_path / "campaign", planned_pairs=2
    )
    evidence = write_benchmark_campaign_evidence(
        tmp_path / "campaign-evidence", campaign.path, pair_paths
    )
    primary_pair = load_counterbalanced_pair(pair_paths[0])

    plan = build_pull_request_plan(
        proposal.path,
        primary_pair.before_after.path,
        primary_pair.path,
        evidence.path,
    )

    assert plan.benchmark_campaign_evidence_id == evidence.artifact_id
    assert plan.benchmark_campaign_id == campaign.campaign_id
    assert plan.benchmark_campaign_pair_ids == evidence.pair_ids
    assert f"- Campaign evidence: `{evidence.artifact_id}`" in plan.body
    assert "Campaign completion: `2/2` pairs" in plan.body
    assert "## Preregistered repeated campaign" in plan.body
    assert "not statistical significance or a confidence interval" in plan.body
    for pair_id in evidence.pair_ids:
        assert f"`{pair_id}`" in plan.body


def test_rejects_campaign_that_does_not_contain_primary_pair(tmp_path: Path) -> None:
    campaign_proposal, campaign, pair_paths = completed_campaign(
        tmp_path / "campaign", planned_pairs=2
    )
    evidence = write_benchmark_campaign_evidence(
        tmp_path / "campaign-evidence", campaign.path, pair_paths
    )
    proposal, benchmark, pair = publication_artifacts(tmp_path / "primary")
    assert proposal.artifact_id == campaign_proposal.artifact_id
    assert pair.artifact_id not in evidence.pair_ids

    with pytest.raises(PullRequestPlanError, match="not included"):
        build_pull_request_plan(
            proposal.path, benchmark.path, pair.path, evidence.path
        )


def test_rejects_primary_benchmark_outside_the_verified_pair(tmp_path: Path) -> None:
    proposal, _, pair = publication_artifacts(tmp_path)
    _, _, run = published_pair(tmp_path / "other")
    run.after.runtime = run.after.runtime.model_copy(
        update={"oom_killed_count": 1}
    )
    run.verdict = compare_benchmarks(run.before, run.after)
    assert run.verdict.status == "fail"
    failed = write_benchmark_result(tmp_path / "failed", run)

    with pytest.raises(PullRequestPlanError, match="primary benchmark"):
        build_pull_request_plan(proposal.path, failed.path, pair.path)


def test_rejects_benchmark_cost_that_conflicts_with_proposal(tmp_path: Path) -> None:
    proposal, _, pair = publication_artifacts(tmp_path)
    _, _, run = published_pair(tmp_path / "other")
    run.after.request_cost_usd += 1
    run.verdict = compare_benchmarks(run.before, run.after)
    mismatched = write_benchmark_result(tmp_path / "mismatched", run)

    with pytest.raises(PullRequestPlanError, match="primary benchmark"):
        build_pull_request_plan(proposal.path, mismatched.path, pair.path)


def test_rejects_benchmark_bound_to_another_proposal(tmp_path: Path) -> None:
    _, benchmark, pair = publication_artifacts(tmp_path)
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
        build_pull_request_plan(other.path, benchmark.path, pair.path)
