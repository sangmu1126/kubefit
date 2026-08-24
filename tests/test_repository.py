import os
import subprocess
from pathlib import Path

import pytest

from gitops import (
    RepositoryCommitError,
    commit_pull_request_plan,
    inspect_repository_plan,
)
from gitops.pull_request import build_pull_request_plan
from tests.test_benchmark_pair_artifact import publication_artifacts


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def verified_plan(root: Path):
    proposal, benchmark, pair = publication_artifacts(root)
    return build_pull_request_plan(proposal.path, benchmark.path, pair.path)


def initialize_repository(root: Path, before_content: str) -> None:
    root.mkdir()
    git(root, "init", "--initial-branch", "main")
    git(root, "config", "user.name", "KubeFit Test")
    git(root, "config", "user.email", "kubefit@example.invalid")
    destination = root / "deploy/demo.yaml"
    destination.parent.mkdir()
    destination.write_text(before_content)
    git(root, "add", "--", "deploy/demo.yaml")
    git(root, "commit", "--message", "initial")


def test_commits_exactly_one_file_and_returns_to_base_idempotently(
    tmp_path: Path,
) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    base_sha = git(repository, "rev-parse", "HEAD")

    first = commit_pull_request_plan(repository, plan)
    second = commit_pull_request_plan(repository, plan)

    assert first.reused is False
    assert second.reused is True
    assert second.commit_sha == first.commit_sha
    assert first.base_branch == "main"
    assert first.base_commit_sha == base_sha
    assert git(repository, "branch", "--show-current") == "main"
    assert git(repository, "status", "--porcelain") == ""
    assert (repository / "deploy/demo.yaml").read_text() == plan.file_change.before_content
    assert (
        git(repository, "diff-tree", "--no-commit-id", "--name-only", "-r", first.commit_sha)
        == "deploy/demo.yaml"
    )
    committed = subprocess.run(
        ["git", "-C", str(repository), "show", f"{first.commit_sha}:deploy/demo.yaml"],
        check=True,
        capture_output=True,
    ).stdout
    assert committed == plan.file_change.after_content.encode()


def test_inspects_absent_and_reusable_plan_without_mutating_checkout(
    tmp_path: Path,
) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    before_head = git(repository, "rev-parse", "HEAD")
    before_bytes = (repository / plan.file_change.path).read_bytes()

    absent = inspect_repository_plan(repository, plan)

    assert absent.local_branch_state == "absent"
    assert absent.local_commit_sha is None
    assert git(repository, "branch", "--show-current") == "main"
    assert git(repository, "rev-parse", "HEAD") == before_head
    assert git(repository, "status", "--porcelain") == ""
    assert (repository / plan.file_change.path).read_bytes() == before_bytes

    commit = commit_pull_request_plan(repository, plan)
    reusable = inspect_repository_plan(repository, plan)

    assert reusable.local_branch_state == "reusable"
    assert reusable.local_commit_sha == commit.commit_sha
    assert git(repository, "branch", "--show-current") == "main"
    assert git(repository, "rev-parse", "HEAD") == before_head
    assert git(repository, "status", "--porcelain") == ""
    assert (repository / plan.file_change.path).read_bytes() == before_bytes


def test_rejects_dirty_repository_without_creating_branch(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    (repository / "notes.txt").write_text("untracked\n")

    with pytest.raises(RepositoryCommitError, match="clean working tree"):
        commit_pull_request_plan(repository, plan)

    assert git(repository, "branch", "--list", plan.branch_name) == ""


def test_rejects_stale_source_bytes_before_creating_branch(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    stale = plan.file_change.before_content.replace('cpu: "1000m"', 'cpu: "900m"')
    initialize_repository(repository, stale)

    with pytest.raises(RepositoryCommitError, match="SHA-256 is stale"):
        commit_pull_request_plan(repository, plan)

    assert git(repository, "branch", "--list", plan.branch_name) == ""


def test_rejects_committed_symlink_target(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "--initial-branch", "main")
    git(repository, "config", "user.name", "KubeFit Test")
    git(repository, "config", "user.email", "kubefit@example.invalid")
    (repository / "deploy").mkdir()
    (repository / "actual.yaml").write_text(plan.file_change.before_content)
    os.symlink("../actual.yaml", repository / "deploy/demo.yaml")
    git(repository, "add", "--", "actual.yaml", "deploy/demo.yaml")
    git(repository, "commit", "--message", "initial symlink")

    with pytest.raises(RepositoryCommitError, match="contains a symlink"):
        commit_pull_request_plan(repository, plan)


def test_rejects_colliding_branch_without_overwriting_it(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    git(repository, "branch", plan.branch_name)
    collision_sha = git(repository, "rev-parse", plan.branch_name)

    with pytest.raises(RepositoryCommitError, match="exactly one commit"):
        commit_pull_request_plan(repository, plan)

    assert git(repository, "rev-parse", plan.branch_name) == collision_sha
    assert git(repository, "branch", "--show-current") == "main"


def test_commit_hook_failure_restores_file_branch_and_checkout(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    hook = repository / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    with pytest.raises(RepositoryCommitError, match="git .*commit.* failed"):
        commit_pull_request_plan(repository, plan)

    assert git(repository, "branch", "--show-current") == "main"
    assert git(repository, "branch", "--list", plan.branch_name) == ""
    assert git(repository, "status", "--porcelain") == ""
    assert (repository / "deploy/demo.yaml").read_text() == plan.file_change.before_content


def test_rejects_existing_branch_that_changes_file_mode(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    git(repository, "switch", "--create", plan.branch_name)
    destination = repository / "deploy/demo.yaml"
    destination.write_text(plan.file_change.after_content)
    destination.chmod(0o755)
    git(repository, "add", "--", plan.file_change.path)
    git(repository, "commit", "--message", plan.commit_message)
    git(repository, "switch", "main")

    with pytest.raises(RepositoryCommitError, match="changed the planned file mode"):
        commit_pull_request_plan(repository, plan)


def test_rejects_detached_head_without_creating_branch(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    git(repository, "switch", "--detach")

    with pytest.raises(RepositoryCommitError, match="attached to a branch"):
        commit_pull_request_plan(repository, plan)

    assert git(repository, "branch", "--list", plan.branch_name) == ""


def test_rejects_symlinked_repository_root(tmp_path: Path) -> None:
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    linked = tmp_path / "linked-repository"
    linked.symlink_to(repository, target_is_directory=True)

    with pytest.raises(RepositoryCommitError, match="not a safe directory"):
        commit_pull_request_plan(linked, plan)

    assert git(repository, "branch", "--list", plan.branch_name) == ""
