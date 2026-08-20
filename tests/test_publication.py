import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError

import pytest

from gitops import (
    GitHubRepository,
    GitHubRestClient,
    PullRequestPublicationError,
    PullRequestRecord,
    RepositoryCommitError,
    SubprocessGitRemote,
    commit_pull_request_plan,
    publish_pull_request,
)
from tests.test_repository import git, initialize_repository, verified_plan


class MemoryRemote:
    def __init__(self) -> None:
        self.identity = GitHubRepository(owner="acme", name="workloads")
        self.branches: dict[str, str] = {}
        self.create_calls = 0
        self.fail_after_create = False

    def repository(self, root: Path, remote: str) -> GitHubRepository:
        assert remote == "origin"
        return self.identity

    def branch_sha(self, root: Path, remote: str, branch: str) -> str | None:
        return self.branches.get(branch)

    def create_branch(
        self, root: Path, remote: str, branch: str, commit_sha: str
    ) -> None:
        self.create_calls += 1
        if branch in self.branches:
            raise AssertionError("publisher tried to overwrite a branch")
        self.branches[branch] = commit_sha
        if self.fail_after_create:
            self.fail_after_create = False
            raise PullRequestPublicationError("simulated lost push response")


class MemoryPullRequests:
    def __init__(self) -> None:
        self.records: list[PullRequestRecord] = []
        self.create_calls = 0
        self.fail_create_once = False
        self.fail_after_create_once = False

    def find_open(
        self,
        repository: GitHubRepository,
        *,
        head_owner: str,
        head_branch: str,
        base_branch: str,
    ) -> list[PullRequestRecord]:
        return [
            record
            for record in self.records
            if record.state == "open"
            and record.head_owner == head_owner
            and record.head_branch == head_branch
            and record.base_branch == base_branch
        ]

    def create_draft(
        self,
        repository: GitHubRepository,
        *,
        head_owner: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestRecord:
        self.create_calls += 1
        if self.fail_create_once:
            self.fail_create_once = False
            raise PullRequestPublicationError("simulated API timeout")
        record = PullRequestRecord(
            number=len(self.records) + 1,
            html_url=f"https://github.com/{repository.owner}/{repository.name}/pull/1",
            state="open",
            draft=True,
            head_owner=head_owner,
            head_branch=head_branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )
        self.records.append(record)
        if self.fail_after_create_once:
            self.fail_after_create_once = False
            raise PullRequestPublicationError("simulated lost API response")
        return record


def prepared_publication(tmp_path: Path):
    plan = verified_plan(tmp_path / "artifacts")
    repository = tmp_path / "repository"
    initialize_repository(repository, plan.file_change.before_content)
    commit = commit_pull_request_plan(repository, plan)
    return repository, plan, commit


def test_publishes_once_then_reuses_branch_and_draft(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    remote = MemoryRemote()
    pull_requests = MemoryPullRequests()

    first = publish_pull_request(
        repository, plan, commit, pull_requests, git_remote=remote
    )
    second = publish_pull_request(
        repository, plan, commit, pull_requests, git_remote=remote
    )

    assert first.branch_reused is False
    assert first.pull_request_reused is False
    assert second.branch_reused is True
    assert second.pull_request_reused is True
    assert second.pull_request_number == first.pull_request_number
    assert remote.branches == {plan.branch_name: commit.commit_sha}
    assert remote.create_calls == 1
    assert pull_requests.create_calls == 1


def test_recovers_when_push_succeeded_but_response_was_lost(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    remote = MemoryRemote()
    remote.fail_after_create = True

    result = publish_pull_request(
        repository, plan, commit, MemoryPullRequests(), git_remote=remote
    )

    assert result.branch_reused is True
    assert remote.branches[plan.branch_name] == commit.commit_sha


def test_retry_after_api_failure_reuses_remote_branch(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    remote = MemoryRemote()
    pull_requests = MemoryPullRequests()
    pull_requests.fail_create_once = True

    with pytest.raises(PullRequestPublicationError, match="was not observed"):
        publish_pull_request(
            repository, plan, commit, pull_requests, git_remote=remote
        )

    result = publish_pull_request(
        repository, plan, commit, pull_requests, git_remote=remote
    )
    assert result.branch_reused is True
    assert result.pull_request_reused is False
    assert remote.create_calls == 1


def test_recovers_when_draft_was_created_but_response_was_lost(
    tmp_path: Path,
) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    remote = MemoryRemote()
    pull_requests = MemoryPullRequests()
    pull_requests.fail_after_create_once = True

    result = publish_pull_request(
        repository, plan, commit, pull_requests, git_remote=remote
    )

    assert result.pull_request_reused is True
    assert result.pull_request_number == 1
    assert pull_requests.create_calls == 1


def test_rejects_remote_branch_collision_without_calling_api(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    remote = MemoryRemote()
    remote.branches[plan.branch_name] = "f" * 40
    pull_requests = MemoryPullRequests()

    with pytest.raises(PullRequestPublicationError, match="refusing to overwrite"):
        publish_pull_request(
            repository, plan, commit, pull_requests, git_remote=remote
        )

    assert remote.branches[plan.branch_name] == "f" * 40
    assert pull_requests.create_calls == 0


def test_rejects_divergent_existing_pull_request(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    remote = MemoryRemote()
    remote.branches[plan.branch_name] = commit.commit_sha
    pull_requests = MemoryPullRequests()
    pull_requests.records.append(
        PullRequestRecord(
            number=7,
            html_url="https://github.com/acme/workloads/pull/7",
            state="open",
            draft=False,
            head_owner="acme",
            head_branch=plan.branch_name,
            base_branch="main",
            title=plan.title,
            body=plan.body,
        )
    )

    with pytest.raises(PullRequestPublicationError, match="does not match"):
        publish_pull_request(
            repository, plan, commit, pull_requests, git_remote=remote
        )

    assert pull_requests.create_calls == 0


def test_rejects_ambiguous_open_pull_requests(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    remote = MemoryRemote()
    remote.branches[plan.branch_name] = commit.commit_sha
    pull_requests = MemoryPullRequests()
    for number in (1, 2):
        pull_requests.records.append(
            PullRequestRecord(
                number=number,
                html_url=f"https://github.com/acme/workloads/pull/{number}",
                state="open",
                draft=True,
                head_owner="acme",
                head_branch=plan.branch_name,
                base_branch="main",
                title=plan.title,
                body=plan.body,
            )
        )

    with pytest.raises(PullRequestPublicationError, match="multiple open"):
        publish_pull_request(
            repository, plan, commit, pull_requests, git_remote=remote
        )


def test_revalidates_local_commit_before_contacting_remote(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    changed_commit = commit.model_copy(update={"commit_sha": "f" * 40})
    remote = MemoryRemote()

    with pytest.raises(RepositoryCommitError, match="does not match the local branch"):
        publish_pull_request(
            repository,
            plan,
            changed_commit,
            MemoryPullRequests(),
            git_remote=remote,
        )

    assert remote.create_calls == 0


@pytest.mark.parametrize(
    ("remote_url", "owner", "name"),
    [
        ("https://github.com/acme/workloads.git", "acme", "workloads"),
        ("git@github.com:acme/workloads.git", "acme", "workloads"),
        ("ssh://git@github.com/acme/workloads.git", "acme", "workloads"),
    ],
)
def test_parses_only_credential_free_github_remotes(
    tmp_path: Path, remote_url: str, owner: str, name: str
) -> None:
    repository, _, _ = prepared_publication(tmp_path)
    git(repository, "remote", "add", "origin", remote_url)

    identity = SubprocessGitRemote().repository(repository, "origin")

    assert identity == GitHubRepository(owner=owner, name=name)


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://token@github.com/acme/workloads.git",
        "https://github.com/acme/workloads.git?token=secret",
        "http://github.com/acme/workloads.git",
        "https://gitlab.com/acme/workloads.git",
        "file:///tmp/workloads.git",
    ],
)
def test_rejects_credentialed_or_non_github_remotes(
    tmp_path: Path, remote_url: str
) -> None:
    repository, _, _ = prepared_publication(tmp_path)
    git(repository, "remote", "add", "origin", remote_url)

    with pytest.raises(PullRequestPublicationError, match="credential-free"):
        SubprocessGitRemote().repository(repository, "origin")


def test_git_remote_creates_only_an_absent_branch(tmp_path: Path) -> None:
    repository, plan, commit = prepared_publication(tmp_path)
    bare_remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch", "main", str(bare_remote)],
        check=True,
        capture_output=True,
    )
    git(repository, "remote", "add", "origin", str(bare_remote))
    gateway = SubprocessGitRemote()

    gateway.create_branch(
        repository, "origin", plan.branch_name, commit.commit_sha
    )

    assert (
        subprocess.run(
            ["git", "--git-dir", str(bare_remote), "rev-parse", plan.branch_name],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == commit.commit_sha
    )
    with pytest.raises(PullRequestPublicationError, match="git push"):
        gateway.create_branch(
            repository, "origin", plan.branch_name, commit.base_commit_sha
        )


def test_github_client_sends_token_only_in_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    token = "secret-test-token"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "number": 3,
                    "html_url": "https://github.com/acme/workloads/pull/3",
                    "state": "open",
                    "draft": True,
                    "head": {"ref": "kubefit/demo", "user": {"login": "acme"}},
                    "base": {"ref": "main"},
                    "title": "title",
                    "body": "body",
                }
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("gitops.publication.urlopen", fake_urlopen)
    client = GitHubRestClient(token)
    record = client.create_draft(
        GitHubRepository(owner="acme", name="workloads"),
        head_owner="acme",
        head_branch="kubefit/demo",
        base_branch="main",
        title="title",
        body="body",
    )

    assert record.number == 3
    assert token not in captured["url"]
    assert token not in captured["body"].decode()
    assert captured["headers"]["Authorization"] == f"Bearer {token}"


def test_github_client_reports_read_only_repository_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "full_name": "acme/workloads",
                    "default_branch": "main",
                    "private": True,
                    "permissions": {
                        "admin": False,
                        "push": True,
                        "pull": True,
                    },
                }
            ).encode()

    monkeypatch.setattr(
        "gitops.publication.urlopen", lambda request, timeout: Response()
    )

    access = GitHubRestClient("test-token").inspect_repository(
        GitHubRepository(owner="acme", name="workloads")
    )

    assert access.default_branch == "main"
    assert access.private is True
    assert access.permissions_reported is True
    assert access.enabled_permissions == ["pull", "push"]


def test_github_http_error_does_not_expose_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "secret-test-token"

    def fail(request, timeout):
        raise HTTPError(
            request.full_url,
            401,
            "unauthorized",
            {},
            None,
        )

    monkeypatch.setattr("gitops.publication.urlopen", fail)
    client = GitHubRestClient(token)

    with pytest.raises(PullRequestPublicationError) as captured:
        client.find_open(
            GitHubRepository(owner="acme", name="workloads"),
            head_owner="acme",
            head_branch="kubefit/demo",
            base_branch="main",
        )

    assert token not in str(captured.value)
