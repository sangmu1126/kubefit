import json
from pathlib import Path

import pytest

import api.cli as cli_module
from benchmarks import (
    load_counterbalanced_pair,
    write_benchmark_campaign_evidence,
)
from gitops import PublicationEvidenceError, verify_publication_evidence
from gitops.pull_request import build_pull_request_plan
from tests.test_benchmark_campaign_artifact import completed_campaign
from tests.test_benchmark_pair_artifact import publication_artifacts


def publication_evidence(
    tmp_path: Path,
    *,
    artifacts=None,
    campaign_evidence=None,
):
    if artifacts is None:
        artifacts = publication_artifacts(tmp_path / "artifacts")
    proposal, benchmark, pair = artifacts
    campaign_path = campaign_evidence.path if campaign_evidence is not None else None
    plan = build_pull_request_plan(
        proposal.path, benchmark.path, pair.path, campaign_path
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    repository = "acme/workloads"
    remote = "kubefit-live-demo"
    commit_sha = "c" * 40
    pull_request_number = 42
    pull_request_url = f"https://github.com/{repository}/pull/{pull_request_number}"
    artifact_check = {
        "name": "artifacts",
        "status": "ready",
        "proposal_id": plan.proposal_id,
        "benchmark_id": plan.benchmark_id,
        "benchmark_pair_id": plan.benchmark_pair_id,
        "benchmark_ids": plan.benchmark_ids,
        "planned_branch": plan.branch_name,
    }
    if campaign_evidence is not None:
        artifact_check.update(
            {
                "benchmark_campaign_evidence_id": (
                    plan.benchmark_campaign_evidence_id
                ),
                "benchmark_campaign_id": plan.benchmark_campaign_id,
                "benchmark_campaign_pair_ids": plan.benchmark_campaign_pair_ids,
            }
        )
    preflight = {
        "schema_version": 1,
        "status": "ready",
        "mutation_performed": False,
        "checks": [
            artifact_check,
            {
                "name": "local_repository",
                "status": "ready",
                "base_branch": "main",
                "base_commit_sha": "a" * 40,
                "planned_path": plan.file_change.path,
                "local_branch_state": "absent",
                "local_commit_sha": None,
            },
            {
                "name": "git_remote",
                "status": "ready",
                "repository": repository,
                "remote": remote,
                "remote_branch_state": "absent",
                "remote_commit_sha": None,
            },
            {
                "name": "github_api",
                "status": "ready",
                "token_env": "GITHUB_TOKEN",
                "token_present": True,
                "repository_readable": True,
                "default_branch": "main",
                "private": True,
                "permissions_reported": True,
                "enabled_permissions": ["pull", "push"],
            },
        ],
        "blockers": [],
        "warnings": [
            "read-only API access does not prove branch or pull-request write permission"
        ],
    }
    first = {
        "repository": repository,
        "remote": remote,
        "branch": plan.branch_name,
        "commit_sha": commit_sha,
        "branch_reused": False,
        "pull_request_number": pull_request_number,
        "pull_request_url": pull_request_url,
        "pull_request_reused": False,
        "draft": True,
    }
    if campaign_evidence is not None:
        first["benchmark_campaign_evidence_id"] = (
            plan.benchmark_campaign_evidence_id
        )
    second = first | {"branch_reused": True, "pull_request_reused": True}
    github = {
        "number": pull_request_number,
        "url": pull_request_url,
        "state": "OPEN",
        "isDraft": True,
        "headRefName": plan.branch_name,
        "headRefOid": commit_sha,
        "baseRefName": "main",
        "title": plan.title,
        "changedFiles": 1,
    }
    if campaign_evidence is not None:
        github["body"] = plan.body
    (evidence / "preflight.json").write_text(json.dumps(preflight))
    (evidence / "first-publish.json").write_text(json.dumps(first))
    (evidence / "second-publish.json").write_text(json.dumps(second))
    (evidence / "remote-ref.txt").write_text(
        f"{commit_sha}\trefs/heads/{plan.branch_name}\n"
    )
    (evidence / "github-pr.json").write_text(json.dumps(github))
    return proposal.path, benchmark.path, pair.path, evidence, plan


def test_verifies_two_run_evidence_deterministically(tmp_path: Path) -> None:
    proposal, benchmark, pair, evidence, plan = publication_evidence(tmp_path)

    first = verify_publication_evidence(proposal, benchmark, pair, evidence)
    second = verify_publication_evidence(proposal, benchmark, pair, evidence)

    assert first == second
    assert first.verification_id.startswith("publication-")
    assert first.proposal_id == plan.proposal_id
    assert first.benchmark_id == plan.benchmark_id
    assert first.benchmark_pair_id == plan.benchmark_pair_id
    assert first.benchmark_ids == plan.benchmark_ids
    assert first.branch == plan.branch_name
    assert first.pull_request_number == 42
    assert set(first.evidence_sha256) == {
        "preflight.json",
        "first-publish.json",
        "second-publish.json",
        "remote-ref.txt",
        "github-pr.json",
    }


def test_verifies_campaign_attachment_and_exact_github_body(tmp_path: Path) -> None:
    proposal, campaign, pairs = completed_campaign(
        tmp_path / "campaign", planned_pairs=2
    )
    campaign_evidence = write_benchmark_campaign_evidence(
        tmp_path / "campaign-artifact", campaign.path, pairs
    )
    primary_pair = load_counterbalanced_pair(pairs[0])
    proposal_path, benchmark_path, pair_path, evidence, plan = publication_evidence(
        tmp_path,
        artifacts=(proposal, primary_pair.before_after, primary_pair),
        campaign_evidence=campaign_evidence,
    )

    verified = verify_publication_evidence(
        proposal_path,
        benchmark_path,
        pair_path,
        evidence,
        campaign_evidence.path,
    )

    assert verified.benchmark_campaign_evidence_id == campaign_evidence.artifact_id
    assert verified.benchmark_campaign_id == campaign.campaign_id
    assert verified.benchmark_campaign_pair_ids == campaign_evidence.pair_ids
    assert verified.verification_id.startswith("publication-")

    github_path = evidence / "github-pr.json"
    github = json.loads(github_path.read_text())
    github["body"] = plan.body + "edited\n"
    github_path.write_text(json.dumps(github))
    with pytest.raises(PublicationEvidenceError, match="body does not match"):
        verify_publication_evidence(
            proposal_path,
            benchmark_path,
            pair_path,
            evidence,
            campaign_evidence.path,
        )


def test_rejects_mixed_publication_outputs(tmp_path: Path) -> None:
    proposal, benchmark, pair, evidence, _ = publication_evidence(tmp_path)
    path = evidence / "second-publish.json"
    payload = json.loads(path.read_text())
    payload["commit_sha"] = "d" * 40
    path.write_text(json.dumps(payload))

    with pytest.raises(PublicationEvidenceError, match="publication commit_sha"):
        verify_publication_evidence(proposal, benchmark, pair, evidence)


@pytest.mark.parametrize("change", ["missing", "unexpected"])
def test_rejects_non_exact_evidence_file_set(tmp_path: Path, change: str) -> None:
    proposal, benchmark, pair, evidence, _ = publication_evidence(tmp_path)
    if change == "missing":
        (evidence / "remote-ref.txt").unlink()
    else:
        (evidence / "notes.txt").write_text("not part of the contract\n")

    with pytest.raises(PublicationEvidenceError, match="file set is invalid"):
        verify_publication_evidence(proposal, benchmark, pair, evidence)


def test_rejects_symlinked_evidence_file(tmp_path: Path) -> None:
    proposal, benchmark, pair, evidence, _ = publication_evidence(tmp_path)
    target = evidence / "github-pr.json"
    content = target.read_bytes()
    target.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(content)
    target.symlink_to(outside)

    with pytest.raises(PublicationEvidenceError, match="non-symlinked"):
        verify_publication_evidence(proposal, benchmark, pair, evidence)


def test_rejects_github_pr_that_does_not_match_plan(tmp_path: Path) -> None:
    proposal, benchmark, pair, evidence, _ = publication_evidence(tmp_path)
    path = evidence / "github-pr.json"
    payload = json.loads(path.read_text())
    payload["title"] = "edited after publication"
    path.write_text(json.dumps(payload))

    with pytest.raises(PublicationEvidenceError, match="title does not match"):
        verify_publication_evidence(proposal, benchmark, pair, evidence)


def test_verify_publication_cli_prints_content_addressed_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proposal, benchmark, pair, evidence, _ = publication_evidence(tmp_path)

    cli_module.main(
        [
            "verify-publication",
            "--proposal",
            str(proposal),
            "--benchmark",
            str(benchmark),
            "--benchmark-pair",
            str(pair),
            "--evidence-dir",
            str(evidence),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["verification_id"].startswith("publication-")
    assert output["pull_request_number"] == 42
