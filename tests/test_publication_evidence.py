import json
from pathlib import Path

import pytest

import api.cli as cli_module
from gitops import PublicationEvidenceError, verify_publication_evidence
from gitops.pull_request import build_pull_request_plan
from tests.test_benchmark_pair_artifact import publication_artifacts


def publication_evidence(tmp_path: Path):
    proposal, benchmark, pair = publication_artifacts(tmp_path / "artifacts")
    plan = build_pull_request_plan(proposal.path, benchmark.path, pair.path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    repository = "acme/workloads"
    remote = "kubefit-live-demo"
    commit_sha = "c" * 40
    pull_request_number = 42
    pull_request_url = f"https://github.com/{repository}/pull/{pull_request_number}"
    preflight = {
        "schema_version": 1,
        "status": "ready",
        "mutation_performed": False,
        "checks": [
            {
                "name": "artifacts",
                "status": "ready",
                "proposal_id": plan.proposal_id,
                "benchmark_id": plan.benchmark_id,
                "benchmark_pair_id": plan.benchmark_pair_id,
                "benchmark_ids": plan.benchmark_ids,
                "planned_branch": plan.branch_name,
            },
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
