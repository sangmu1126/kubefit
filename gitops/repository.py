import hashlib
import os
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gitops.pull_request import PullRequestPlan


class RepositoryCommitError(RuntimeError):
    """Raised when a pull request plan cannot be committed without widening scope."""

    def __init__(self, message: str, cleanup_error: Exception | None = None) -> None:
        self.cleanup_error = cleanup_error
        if cleanup_error is not None:
            message += f"; repository cleanup also failed: {cleanup_error}"
        super().__init__(message)


class RepositoryCommit(BaseModel):
    model_config = ConfigDict(frozen=True)

    branch_name: str
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_branch: str
    base_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    file_path: str
    reused: bool


class RepositoryPlanInspection(BaseModel):
    model_config = ConfigDict(frozen=True)

    repository_root: Path
    base_branch: str
    base_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch_name: str
    file_path: str
    local_branch_state: Literal["absent", "reusable"]
    local_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")


def inspect_repository_plan(
    repository_root: Path,
    plan: PullRequestPlan,
) -> RepositoryPlanInspection:
    """Validate local publication prerequisites without mutating the repository."""
    root = _validate_repository_root(repository_root)
    _require_clean(root)
    base_branch = _current_branch(root)
    base_sha = _git(root, "rev-parse", "HEAD")
    _validate_planned_file(root, plan)
    local_commit_sha = None
    local_branch_state: Literal["absent", "reusable"] = "absent"
    if _branch_exists(root, plan.branch_name):
        local_commit_sha = _validate_plan_commit(root, plan, base_sha)
        local_branch_state = "reusable"
    return RepositoryPlanInspection(
        repository_root=root,
        base_branch=base_branch,
        base_commit_sha=base_sha,
        branch_name=plan.branch_name,
        file_path=plan.file_change.path,
        local_branch_state=local_branch_state,
        local_commit_sha=local_commit_sha,
    )


def validate_repository_commit(
    repository_root: Path,
    plan: PullRequestPlan,
    commit: RepositoryCommit,
) -> Path:
    """Revalidate a commit handoff immediately before an external publication."""
    root = _validate_repository_root(repository_root)
    _require_clean(root)
    if _current_branch(root) != commit.base_branch:
        raise RepositoryCommitError("repository is not on the recorded base branch")
    if _git(root, "rev-parse", "HEAD") != commit.base_commit_sha:
        raise RepositoryCommitError("repository base commit has moved since planning")
    if commit.branch_name != plan.branch_name:
        raise RepositoryCommitError("repository commit branch does not match the plan")
    if commit.file_path != plan.file_change.path:
        raise RepositoryCommitError("repository commit path does not match the plan")
    _validate_planned_file(root, plan)
    if not _branch_exists(root, plan.branch_name):
        raise RepositoryCommitError("planned local branch no longer exists")
    validated_sha = _validate_plan_commit(root, plan, commit.base_commit_sha)
    if validated_sha != commit.commit_sha:
        raise RepositoryCommitError("repository commit SHA does not match the local branch")
    return root


def commit_pull_request_plan(
    repository_root: Path,
    plan: PullRequestPlan,
) -> RepositoryCommit:
    root = _validate_repository_root(repository_root)
    _require_clean(root)
    base_branch = _current_branch(root)
    base_sha = _git(root, "rev-parse", "HEAD")
    destination = _validate_planned_file(root, plan)

    if _branch_exists(root, plan.branch_name):
        commit_sha = _validate_plan_commit(root, plan, base_sha)
        return RepositoryCommit(
            branch_name=plan.branch_name,
            commit_sha=commit_sha,
            base_branch=base_branch,
            base_commit_sha=base_sha,
            file_path=plan.file_change.path,
            reused=True,
        )

    branch_created = False
    try:
        _git(root, "switch", "--create", plan.branch_name)
        branch_created = True
        _atomic_replace(destination, plan.file_change.after_content.encode())
        _git(root, "add", "--", plan.file_change.path)
        staged = _git_bytes(
            root, "diff", "--cached", "--name-only", "-z"
        ).split(b"\0")
        staged_paths = [item.decode() for item in staged if item]
        if staged_paths != [plan.file_change.path]:
            raise RepositoryCommitError(
                f"staged paths do not match the plan: {staged_paths}"
            )
        _git(
            root,
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--message",
            plan.commit_message,
            "--only",
            "--",
            plan.file_change.path,
        )
        commit_sha = _validate_plan_commit(root, plan, base_sha)
        _git(root, "switch", base_branch)
        _require_clean(root)
    except Exception as exc:
        cleanup_error = None
        if branch_created:
            try:
                _cleanup_created_branch(
                    root,
                    plan,
                    destination,
                    base_branch,
                )
            except Exception as cleanup_exc:
                cleanup_error = cleanup_exc
        if isinstance(exc, RepositoryCommitError) and cleanup_error is None:
            raise
        raise RepositoryCommitError(str(exc), cleanup_error) from exc

    return RepositoryCommit(
        branch_name=plan.branch_name,
        commit_sha=commit_sha,
        base_branch=base_branch,
        base_commit_sha=base_sha,
        file_path=plan.file_change.path,
        reused=False,
    )


def _validate_repository_root(repository_root: Path) -> Path:
    if repository_root.is_symlink() or not repository_root.is_dir():
        raise RepositoryCommitError(
            f"repository root is not a safe directory: {repository_root}"
        )
    root = repository_root.resolve()
    top_level = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise RepositoryCommitError("repository root must be the Git top-level directory")
    return root


def _require_clean(root: Path) -> None:
    status_output = _git_bytes(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if status_output:
        raise RepositoryCommitError("repository must have a clean working tree")


def _current_branch(root: Path) -> str:
    try:
        return _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    except RepositoryCommitError as exc:
        raise RepositoryCommitError("repository HEAD must be attached to a branch") from exc


def _validate_planned_file(root: Path, plan: PullRequestPlan) -> Path:
    relative = PurePosixPath(plan.file_change.path)
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or "\n" in plan.file_change.path
        or "\r" in plan.file_change.path
    ):
        raise RepositoryCommitError("planned file path must be repository-relative")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if os.path.lexists(cursor) and cursor.is_symlink():
            raise RepositoryCommitError(f"planned file path contains a symlink: {cursor}")
    if not cursor.is_file():
        raise RepositoryCommitError("planned repository file must be a regular file")
    try:
        cursor.resolve().relative_to(root)
    except ValueError as exc:
        raise RepositoryCommitError("planned file resolves outside the repository") from exc
    before = cursor.read_bytes()
    if hashlib.sha256(before).hexdigest() != plan.file_change.expected_before_sha256:
        raise RepositoryCommitError("planned repository file SHA-256 is stale")
    if before != plan.file_change.before_content.encode():
        raise RepositoryCommitError("planned repository file bytes are stale")
    if before == plan.file_change.after_content.encode():
        raise RepositoryCommitError("planned repository file is already patched")
    return cursor


def _branch_exists(root: Path, branch_name: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        capture_output=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RepositoryCommitError(
            f"git show-ref failed: {_command_error(result)}"
        )
    return result.returncode == 0


def _validate_plan_commit(root: Path, plan: PullRequestPlan, base_sha: str) -> str:
    commit_sha = _git(root, "rev-parse", f"refs/heads/{plan.branch_name}")
    ancestry = _git(root, "rev-list", "--parents", "-n", "1", commit_sha).split()
    if ancestry != [commit_sha, base_sha]:
        raise RepositoryCommitError(
            "generated branch must contain exactly one commit on the current base"
        )
    changed = _git_bytes(
        root,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "-z",
        commit_sha,
    ).split(b"\0")
    changed_paths = [item.decode() for item in changed if item]
    if changed_paths != [plan.file_change.path]:
        raise RepositoryCommitError(
            f"generated commit paths do not match the plan: {changed_paths}"
        )
    base_mode = _tree_mode(root, base_sha, plan.file_change.path)
    committed_mode = _tree_mode(root, commit_sha, plan.file_change.path)
    if committed_mode != base_mode:
        raise RepositoryCommitError("generated commit changed the planned file mode")
    committed = _git_bytes(
        root,
        "show",
        f"{commit_sha}:{plan.file_change.path}",
    )
    if committed != plan.file_change.after_content.encode():
        raise RepositoryCommitError("generated commit content does not match the plan")
    subject = _git(root, "log", "-1", "--format=%s", commit_sha)
    if subject != plan.commit_message:
        raise RepositoryCommitError("generated commit subject does not match the plan")
    return commit_sha


def _tree_mode(root: Path, revision: str, path: str) -> str:
    entry = _git(root, "ls-tree", revision, "--", path)
    if not entry:
        raise RepositoryCommitError(f"planned file is missing from Git tree {revision}")
    return entry.split(maxsplit=1)[0]


def _cleanup_created_branch(
    root: Path,
    plan: PullRequestPlan,
    destination: Path,
    base_branch: str,
) -> None:
    if _current_branch(root) == plan.branch_name:
        if _git_bytes(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ):
            _atomic_replace(destination, plan.file_change.before_content.encode())
            _git(root, "restore", "--staged", "--", plan.file_change.path)
            _require_clean(root)
        _git(root, "switch", base_branch)
    _git(root, "branch", "--delete", "--force", plan.branch_name)
    _require_clean(root)


def _atomic_replace(destination: Path, content: bytes) -> None:
    existing_mode = stat.S_IMODE(destination.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.kubefit-",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(existing_mode)
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _git(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode().strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = _command_error(exc)
        else:
            detail = str(exc)
        raise RepositoryCommitError(f"git {' '.join(args)} failed: {detail}") from exc
    return result.stdout


def _command_error(
    result: subprocess.CompletedProcess[bytes] | subprocess.CalledProcessError,
) -> str:
    stderr = result.stderr.decode(errors="replace").strip() if result.stderr else ""
    return stderr or f"exit status {result.returncode}"
