from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from gitops.pull_request import PullRequestPlan
from gitops.repository import RepositoryCommit, validate_repository_commit


class PullRequestPublicationError(RuntimeError):
    """Raised when publication cannot preserve the verified review contract."""


class GitHubRepository(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
    name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")


class PullRequestRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int = Field(gt=0)
    html_url: str = Field(pattern=r"^https://github\.com/")
    state: str
    draft: bool
    head_owner: str
    head_branch: str
    base_branch: str
    title: str
    body: str


class PublishedPullRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    repository: GitHubRepository
    remote: str
    branch_name: str
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch_reused: bool
    pull_request_number: int = Field(gt=0)
    pull_request_url: str = Field(pattern=r"^https://github\.com/")
    pull_request_reused: bool


class GitRemoteGateway(Protocol):
    def repository(self, root: Path, remote: str) -> GitHubRepository: ...

    def branch_sha(self, root: Path, remote: str, branch: str) -> str | None: ...

    def create_branch(
        self, root: Path, remote: str, branch: str, commit_sha: str
    ) -> None: ...


class PullRequestGateway(Protocol):
    def find_open(
        self,
        repository: GitHubRepository,
        *,
        head_owner: str,
        head_branch: str,
        base_branch: str,
    ) -> list[PullRequestRecord]: ...

    def create_draft(
        self,
        repository: GitHubRepository,
        *,
        head_owner: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestRecord: ...


def publish_pull_request(
    repository_root: Path,
    plan: PullRequestPlan,
    commit: RepositoryCommit,
    pull_requests: PullRequestGateway,
    *,
    remote: str = "origin",
    git_remote: GitRemoteGateway | None = None,
) -> PublishedPullRequest:
    root = validate_repository_commit(repository_root, plan, commit)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
        raise PullRequestPublicationError("remote name contains unsafe characters")
    remote_gateway = git_remote or SubprocessGitRemote()
    repository = remote_gateway.repository(root, remote)

    remote_sha = remote_gateway.branch_sha(root, remote, plan.branch_name)
    branch_reused = remote_sha is not None
    if remote_sha is not None and remote_sha != commit.commit_sha:
        raise PullRequestPublicationError(
            "remote branch exists at a different commit; refusing to overwrite it"
        )
    if remote_sha is None:
        try:
            remote_gateway.create_branch(
                root, remote, plan.branch_name, commit.commit_sha
            )
        except Exception as exc:
            observed_sha = remote_gateway.branch_sha(root, remote, plan.branch_name)
            if observed_sha != commit.commit_sha:
                raise PullRequestPublicationError(
                    "remote branch creation failed and the verified commit was not observed"
                ) from exc
            branch_reused = True

    matches = pull_requests.find_open(
        repository,
        head_owner=repository.owner,
        head_branch=plan.branch_name,
        base_branch=commit.base_branch,
    )
    if len(matches) > 1:
        raise PullRequestPublicationError(
            "multiple open pull requests match the planned head and base"
        )
    if matches:
        record = matches[0]
        _validate_pull_request(record, repository, plan, commit)
        pull_request_reused = True
    else:
        try:
            record = pull_requests.create_draft(
                repository,
                head_owner=repository.owner,
                head_branch=plan.branch_name,
                base_branch=commit.base_branch,
                title=plan.title,
                body=plan.body,
            )
            pull_request_reused = False
        except Exception as exc:
            observed = pull_requests.find_open(
                repository,
                head_owner=repository.owner,
                head_branch=plan.branch_name,
                base_branch=commit.base_branch,
            )
            if len(observed) != 1:
                raise PullRequestPublicationError(
                    "draft creation failed and one matching pull request was not observed"
                ) from exc
            record = observed[0]
            pull_request_reused = True
        _validate_pull_request(record, repository, plan, commit)

    return PublishedPullRequest(
        repository=repository,
        remote=remote,
        branch_name=plan.branch_name,
        commit_sha=commit.commit_sha,
        branch_reused=branch_reused,
        pull_request_number=record.number,
        pull_request_url=record.html_url,
        pull_request_reused=pull_request_reused,
    )


class SubprocessGitRemote:
    def repository(self, root: Path, remote: str) -> GitHubRepository:
        remote_url = _git(root, "remote", "get-url", "--push", remote)
        return _parse_github_remote(remote_url)

    def branch_sha(self, root: Path, remote: str, branch: str) -> str | None:
        ref = f"refs/heads/{branch}"
        output = _git(root, "ls-remote", "--refs", remote, ref)
        if not output:
            return None
        lines = output.splitlines()
        if len(lines) != 1:
            raise PullRequestPublicationError("remote returned an ambiguous branch ref")
        fields = lines[0].split()
        if len(fields) != 2 or fields[1] != ref or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
            raise PullRequestPublicationError("remote returned an invalid branch ref")
        return fields[0]

    def create_branch(
        self, root: Path, remote: str, branch: str, commit_sha: str
    ) -> None:
        ref = f"refs/heads/{branch}"
        _git(
            root,
            "push",
            "--porcelain",
            f"--force-with-lease={ref}:",
            remote,
            f"{commit_sha}:{ref}",
        )


class GitHubRestClient:
    """Small GitHub REST boundary; the token is used only in an HTTP header."""

    def __init__(
        self,
        token: str,
        *,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 15.0,
    ) -> None:
        if not token or "\r" in token or "\n" in token:
            raise PullRequestPublicationError("GitHub token is missing or invalid")
        if api_base_url.rstrip("/") != "https://api.github.com":
            raise PullRequestPublicationError("only the public GitHub API is supported")
        if timeout_seconds <= 0:
            raise PullRequestPublicationError("GitHub API timeout must be positive")
        self._token = token
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def find_open(
        self,
        repository: GitHubRepository,
        *,
        head_owner: str,
        head_branch: str,
        base_branch: str,
    ) -> list[PullRequestRecord]:
        query = urlencode(
            {
                "state": "open",
                "head": f"{head_owner}:{head_branch}",
                "base": base_branch,
                "per_page": 100,
            }
        )
        payload = self._request(
            "GET", f"/repos/{repository.owner}/{repository.name}/pulls?{query}"
        )
        if not isinstance(payload, list):
            raise PullRequestPublicationError("GitHub pull request list is invalid")
        return [_parse_pull_request(item) for item in payload]

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
        payload = self._request(
            "POST",
            f"/repos/{repository.owner}/{repository.name}/pulls",
            {
                "title": title,
                "body": body,
                "head": f"{head_owner}:{head_branch}",
                "base": base_branch,
                "draft": True,
            },
        )
        return _parse_pull_request(payload)

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> object:
        encoded = None if payload is None else json.dumps(payload).encode()
        request = Request(
            f"{self._api_base_url}{path}",
            data=encoded,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "KubeFit/0.1",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            detail = _github_error_detail(exc).replace(self._token, "[REDACTED]")
            raise PullRequestPublicationError(
                f"GitHub API returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            detail = str(exc).replace(self._token, "[REDACTED]")
            raise PullRequestPublicationError(f"GitHub API request failed: {detail}") from exc


def _validate_pull_request(
    record: PullRequestRecord,
    repository: GitHubRepository,
    plan: PullRequestPlan,
    commit: RepositoryCommit,
) -> None:
    expected = {
        "state": "open",
        "draft": True,
        "head_owner": repository.owner,
        "head_branch": plan.branch_name,
        "base_branch": commit.base_branch,
        "title": plan.title,
        "body": plan.body,
    }
    actual = {field: getattr(record, field) for field in expected}
    if actual != expected:
        raise PullRequestPublicationError(
            "GitHub pull request does not match the verified draft contract"
        )
    expected_url = (
        f"https://github.com/{repository.owner}/{repository.name}/pull/{record.number}"
    )
    if record.html_url != expected_url:
        raise PullRequestPublicationError(
            "GitHub pull request URL does not match the remote repository"
        )


def _parse_github_remote(remote_url: str) -> GitHubRepository:
    if "\r" in remote_url or "\n" in remote_url:
        raise PullRequestPublicationError("Git remote URL contains unsafe characters")
    owner: str | None = None
    name: str | None = None
    scp_match = re.fullmatch(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?", remote_url)
    if scp_match:
        owner, name = scp_match.groups()
    else:
        parsed = urlsplit(remote_url)
        if (
            parsed.scheme == "https"
            and parsed.hostname == "github.com"
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        ) or (
            parsed.scheme == "ssh"
            and parsed.hostname == "github.com"
            and parsed.port in {None, 22}
            and parsed.username == "git"
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        ):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 2:
                owner, name = parts
                if name.endswith(".git"):
                    name = name[:-4]
    if owner is None or name is None:
        raise PullRequestPublicationError(
            "remote must be a credential-free github.com HTTPS or SSH URL"
        )
    try:
        return GitHubRepository(owner=owner, name=name)
    except ValueError as exc:
        raise PullRequestPublicationError("GitHub repository identity is invalid") from exc


def _parse_pull_request(payload: object) -> PullRequestRecord:
    if not isinstance(payload, dict):
        raise PullRequestPublicationError("GitHub pull request response is invalid")
    try:
        head = payload["head"]
        base = payload["base"]
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise TypeError
        user = head["user"]
        if not isinstance(user, dict):
            raise TypeError
        return PullRequestRecord(
            number=payload["number"],
            html_url=payload["html_url"],
            state=payload["state"],
            draft=payload["draft"],
            head_owner=user["login"],
            head_branch=head["ref"],
            base_branch=base["ref"],
            title=payload["title"],
            body=payload.get("body") or "",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PullRequestPublicationError(
            "GitHub pull request response is invalid"
        ) from exc


def _github_error_detail(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read())
    except (OSError, json.JSONDecodeError):
        return "request rejected"
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"][:300]
    return "request rejected"


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or "").strip() or f"exit status {exc.returncode}"
        else:
            detail = str(exc)
        raise PullRequestPublicationError(
            f"git {' '.join(args[:2])} failed: {detail}"
        ) from exc
    return result.stdout.strip()
