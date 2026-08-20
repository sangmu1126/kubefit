"""Manifest patching and GitHub pull request adapters."""

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
    "generate_resource_patch",
]
