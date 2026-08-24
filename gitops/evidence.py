from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gitops.pull_request import build_pull_request_plan

EVIDENCE_FILES = frozenset(
    {
        "preflight.json",
        "first-publish.json",
        "second-publish.json",
        "remote-ref.txt",
        "github-pr.json",
    }
)


class PublicationEvidenceError(RuntimeError):
    """Raised when captured publication evidence is incomplete or inconsistent."""


class _PreflightEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    status: Literal["ready"]
    mutation_performed: Literal[False]
    checks: list[dict[str, Any]]
    blockers: list[str] = Field(max_length=0)
    warnings: list[str]


class _PublishEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
    remote: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    branch: str = Field(pattern=r"^kubefit/[a-z0-9][a-z0-9._-]*$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch_reused: bool
    pull_request_number: int = Field(gt=0)
    pull_request_url: str = Field(pattern=r"^https://github\.com/")
    pull_request_reused: bool
    draft: Literal[True]
    benchmark_campaign_evidence_id: str | None = Field(
        default=None,
        pattern=r"^benchmark-campaign-evidence-[0-9a-f]{32}$",
    )


class _GitHubPullRequestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int = Field(gt=0)
    url: str = Field(pattern=r"^https://github\.com/")
    state: Literal["OPEN"]
    isDraft: Literal[True]
    headRefName: str
    headRefOid: str = Field(pattern=r"^[0-9a-f]{40}$")
    baseRefName: str
    title: str
    body: str | None = None
    changedFiles: Literal[1]


class VerifiedPublicationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[2] = 2
    verification_id: str = Field(pattern=r"^publication-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    benchmark_id: str = Field(pattern=r"^benchmark-[0-9a-f]{32}$")
    benchmark_pair_id: str = Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")
    benchmark_ids: list[str] = Field(min_length=2, max_length=2)
    benchmark_campaign_evidence_id: str | None = Field(
        default=None,
        pattern=r"^benchmark-campaign-evidence-[0-9a-f]{32}$",
    )
    benchmark_campaign_id: str | None = Field(
        default=None,
        pattern=r"^benchmark-campaign-[0-9a-f]{32}$",
    )
    benchmark_campaign_pair_ids: list[
        Annotated[str, Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")]
    ] | None = Field(default=None, min_length=2, max_length=100)
    repository: str
    remote: str
    base_branch: str
    branch: str
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pull_request_number: int = Field(gt=0)
    pull_request_url: str
    evidence_sha256: dict[str, str]


def verify_publication_evidence(
    proposal_path: Path,
    benchmark_path: Path,
    benchmark_pair_path: Path,
    evidence_directory: Path,
    benchmark_campaign_evidence_path: Path | None = None,
) -> VerifiedPublicationEvidence:
    plan = build_pull_request_plan(
        proposal_path,
        benchmark_path,
        benchmark_pair_path,
        benchmark_campaign_evidence_path,
    )
    files = _load_exact_evidence(evidence_directory)
    preflight = _parse_model(
        _PreflightEvidence, files["preflight.json"], "preflight.json"
    )
    first = _parse_model(
        _PublishEvidence, files["first-publish.json"], "first-publish.json"
    )
    second = _parse_model(
        _PublishEvidence, files["second-publish.json"], "second-publish.json"
    )
    github = _parse_model(
        _GitHubPullRequestEvidence, files["github-pr.json"], "github-pr.json"
    )

    checks = _preflight_checks(preflight)
    artifacts = checks["artifacts"]
    local = checks["local_repository"]
    remote = checks["git_remote"]
    api = checks["github_api"]
    _require_equal(artifacts.get("proposal_id"), plan.proposal_id, "preflight proposal ID")
    _require_equal(artifacts.get("benchmark_id"), plan.benchmark_id, "preflight benchmark ID")
    _require_equal(
        artifacts.get("benchmark_pair_id"),
        plan.benchmark_pair_id,
        "preflight benchmark pair ID",
    )
    _require_equal(
        artifacts.get("benchmark_ids"),
        plan.benchmark_ids,
        "preflight benchmark IDs",
    )
    _require_equal(
        artifacts.get("benchmark_campaign_evidence_id"),
        plan.benchmark_campaign_evidence_id,
        "preflight benchmark campaign evidence ID",
    )
    _require_equal(
        artifacts.get("benchmark_campaign_id"),
        plan.benchmark_campaign_id,
        "preflight benchmark campaign ID",
    )
    _require_equal(
        artifacts.get("benchmark_campaign_pair_ids"),
        plan.benchmark_campaign_pair_ids,
        "preflight benchmark campaign pair IDs",
    )
    _require_equal(artifacts.get("planned_branch"), plan.branch_name, "preflight branch")
    _require_equal(local.get("planned_path"), plan.file_change.path, "preflight file path")
    _require_equal(local.get("local_branch_state"), "absent", "initial local branch state")
    _require_equal(local.get("local_commit_sha"), None, "initial local commit SHA")
    base_branch = _required_string(local.get("base_branch"), "preflight base branch")
    _required_sha(local.get("base_commit_sha"), "preflight base commit SHA")
    _require_equal(remote.get("remote_branch_state"), "absent", "initial remote branch state")
    _require_equal(remote.get("remote_commit_sha"), None, "initial remote commit SHA")
    _require_equal(api.get("token_present"), True, "preflight token presence")
    _require_equal(api.get("repository_readable"), True, "preflight repository access")

    if first.branch_reused or first.pull_request_reused:
        raise PublicationEvidenceError("first publication must create the branch and pull request")
    if not second.branch_reused or not second.pull_request_reused:
        raise PublicationEvidenceError("second publication must reuse the branch and pull request")
    for field in (
        "repository",
        "remote",
        "branch",
        "commit_sha",
        "pull_request_number",
        "pull_request_url",
    ):
        _require_equal(
            getattr(second, field), getattr(first, field), f"publication {field}"
        )
    _require_equal(first.branch, plan.branch_name, "published branch")
    _require_equal(
        first.benchmark_campaign_evidence_id,
        plan.benchmark_campaign_evidence_id,
        "published benchmark campaign evidence ID",
    )
    _require_equal(
        second.benchmark_campaign_evidence_id,
        first.benchmark_campaign_evidence_id,
        "publication benchmark campaign evidence ID",
    )
    _require_equal(remote.get("repository"), first.repository, "preflight repository")
    _require_equal(remote.get("remote"), first.remote, "preflight remote")

    expected_url = (
        f"https://github.com/{first.repository}/pull/{first.pull_request_number}"
    )
    _require_equal(first.pull_request_url, expected_url, "pull request URL")
    _require_equal(github.number, first.pull_request_number, "GitHub pull request number")
    _require_equal(github.url, first.pull_request_url, "GitHub pull request URL")
    _require_equal(github.headRefName, first.branch, "GitHub head branch")
    _require_equal(github.headRefOid, first.commit_sha, "GitHub head SHA")
    _require_equal(github.baseRefName, base_branch, "GitHub base branch")
    _require_equal(github.title, plan.title, "GitHub pull request title")
    if plan.benchmark_campaign_evidence_id is not None and github.body is None:
        raise PublicationEvidenceError(
            "GitHub pull request body is required for campaign evidence verification"
        )
    if github.body is not None:
        _require_equal(github.body, plan.body, "GitHub pull request body")

    expected_ref = f"{first.commit_sha}\trefs/heads/{first.branch}\n".encode()
    _require_equal(files["remote-ref.txt"], expected_ref, "remote branch ref")

    hashes = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in sorted(files.items())
    }
    identity_fields = {
        "proposal_id": plan.proposal_id,
        "benchmark_id": plan.benchmark_id,
        "benchmark_pair_id": plan.benchmark_pair_id,
        "benchmark_ids": plan.benchmark_ids,
        "evidence_sha256": hashes,
    }
    if plan.benchmark_campaign_evidence_id is not None:
        identity_fields.update(
            {
                "benchmark_campaign_evidence_id": (
                    plan.benchmark_campaign_evidence_id
                ),
                "benchmark_campaign_id": plan.benchmark_campaign_id,
                "benchmark_campaign_pair_ids": plan.benchmark_campaign_pair_ids,
            }
        )
    identity = json.dumps(
        identity_fields,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    verification_id = f"publication-{hashlib.sha256(identity).hexdigest()[:32]}"
    return VerifiedPublicationEvidence(
        verification_id=verification_id,
        proposal_id=plan.proposal_id,
        benchmark_id=plan.benchmark_id,
        benchmark_pair_id=plan.benchmark_pair_id,
        benchmark_ids=plan.benchmark_ids,
        benchmark_campaign_evidence_id=plan.benchmark_campaign_evidence_id,
        benchmark_campaign_id=plan.benchmark_campaign_id,
        benchmark_campaign_pair_ids=plan.benchmark_campaign_pair_ids,
        repository=first.repository,
        remote=first.remote,
        base_branch=base_branch,
        branch=first.branch,
        commit_sha=first.commit_sha,
        pull_request_number=first.pull_request_number,
        pull_request_url=first.pull_request_url,
        evidence_sha256=hashes,
    )


def _load_exact_evidence(directory: Path) -> dict[str, bytes]:
    if directory.is_symlink() or not directory.is_dir():
        raise PublicationEvidenceError("evidence root must be a regular, non-symlink directory")
    root = directory.resolve()
    names = {entry.name for entry in root.iterdir()}
    if names != EVIDENCE_FILES:
        missing = sorted(EVIDENCE_FILES - names)
        unexpected = sorted(names - EVIDENCE_FILES)
        raise PublicationEvidenceError(
            f"evidence file set is invalid; missing={missing}, unexpected={unexpected}"
        )
    loaded: dict[str, bytes] = {}
    for name in sorted(EVIDENCE_FILES):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise PublicationEvidenceError(
                f"evidence file must be regular and non-symlinked: {name}"
            )
        try:
            if not os.path.samefile(path.parent, root):
                raise PublicationEvidenceError(f"evidence file escaped its root: {name}")
            loaded[name] = path.read_bytes()
        except OSError as exc:
            raise PublicationEvidenceError(f"cannot read evidence file: {name}") from exc
    return loaded


def _parse_model(model_type, content: bytes, name: str):
    try:
        return model_type.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        raise PublicationEvidenceError(f"evidence JSON is invalid: {name}") from exc


def _preflight_checks(preflight: _PreflightEvidence) -> dict[str, dict[str, Any]]:
    expected_names = ["artifacts", "local_repository", "git_remote", "github_api"]
    names = [check.get("name") for check in preflight.checks]
    if names != expected_names:
        raise PublicationEvidenceError("preflight checks are missing, reordered, or duplicated")
    result: dict[str, dict[str, Any]] = {}
    for check in preflight.checks:
        name = check["name"]
        if check.get("status") != "ready":
            raise PublicationEvidenceError(f"preflight check is not ready: {name}")
        result[name] = check
    return result


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise PublicationEvidenceError(f"{label} is invalid")
    return value


def _required_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise PublicationEvidenceError(f"{label} is invalid")
    return value


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise PublicationEvidenceError(f"{label} does not match")
