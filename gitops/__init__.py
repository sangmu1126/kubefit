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
    "extract_target_document",
    "generate_resource_patch",
    "load_proposal_bundle",
    "load_manifest_sources",
    "write_proposal_bundle",
]
