"""Manifest patching and GitHub pull request adapters."""

from gitops.bundle import (
    BenchmarkContext,
    LoadedProposalBundle,
    ProposalBundle,
    ProposalBundleError,
    load_proposal_bundle,
    write_proposal_bundle,
)
from gitops.manifest import (
    ManifestPatch,
    ManifestPatchError,
    ManifestPatchReport,
    ManifestSource,
    ManifestTarget,
    ResourceChange,
    extract_target_document,
    generate_resource_patch,
)
from gitops.publication import (
    GitHubRepository,
    GitHubRepositoryAccess,
    GitHubRestClient,
    PublishedPullRequest,
    PullRequestPublicationError,
    PullRequestRecord,
    SubprocessGitRemote,
    publish_pull_request,
)
from gitops.pull_request import (
    PullRequestPlan,
    PullRequestPlanError,
    RepositoryFileChange,
    build_pull_request_plan,
)
from gitops.repository import (
    RepositoryCommit,
    RepositoryCommitError,
    RepositoryPlanInspection,
    commit_pull_request_plan,
    inspect_repository_plan,
    validate_repository_commit,
)
from gitops.source import ManifestSourceError, load_manifest_sources

__all__ = [
    "ManifestPatch",
    "ManifestPatchError",
    "ManifestPatchReport",
    "ManifestSource",
    "ManifestSourceError",
    "ManifestTarget",
    "ResourceChange",
    "BenchmarkContext",
    "LoadedProposalBundle",
    "ProposalBundle",
    "ProposalBundleError",
    "PullRequestPlan",
    "PullRequestPlanError",
    "RepositoryFileChange",
    "RepositoryCommit",
    "RepositoryCommitError",
    "RepositoryPlanInspection",
    "GitHubRepository",
    "GitHubRepositoryAccess",
    "GitHubRestClient",
    "PublishedPullRequest",
    "PullRequestPublicationError",
    "PullRequestRecord",
    "SubprocessGitRemote",
    "build_pull_request_plan",
    "commit_pull_request_plan",
    "inspect_repository_plan",
    "publish_pull_request",
    "validate_repository_commit",
    "extract_target_document",
    "generate_resource_patch",
    "load_proposal_bundle",
    "load_manifest_sources",
    "write_proposal_bundle",
]
