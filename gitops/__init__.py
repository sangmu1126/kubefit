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
    "LoadedProposalBundle",
    "ProposalBundle",
    "ProposalBundleError",
    "generate_resource_patch",
    "load_proposal_bundle",
    "write_proposal_bundle",
]
