"""Manifest patching and GitHub pull request adapters."""

from gitops.bundle import (
    BenchmarkContext,
    ProposalBundle,
    ProposalBundleError,
    write_proposal_bundle,
)
from gitops.manifest import (
    ManifestPatch,
    ManifestPatchError,
    ManifestPatchReport,
    ManifestSource,
    ManifestTarget,
    ResourceChange,
    generate_resource_patch,
)

__all__ = [
    "ManifestPatch",
    "ManifestPatchError",
    "ManifestPatchReport",
    "ManifestSource",
    "ManifestTarget",
    "ResourceChange",
    "BenchmarkContext",
    "ProposalBundle",
    "ProposalBundleError",
    "generate_resource_patch",
    "write_proposal_bundle",
]
