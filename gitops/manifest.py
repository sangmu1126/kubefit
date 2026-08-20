import hashlib
import json
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import PurePosixPath

import yaml
from pydantic import BaseModel, Field
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from collector import parse_cpu_millicores, parse_memory_mib
from collector.kubernetes import KubernetesCollectionError
from evaluator import EvaluationResult
from recommender import CurrentResources


class ManifestPatchError(RuntimeError):
    """Raised when a manifest cannot be patched without ambiguity or stale input."""


@dataclass(frozen=True)
class ManifestSource:
    path: str
    content: str

    def __post_init__(self) -> None:
        parsed = PurePosixPath(self.path)
        if (
            not self.path
            or "\n" in self.path
            or "\r" in self.path
            or parsed.is_absolute()
            or ".." in parsed.parts
        ):
            raise ValueError("manifest source path must be a safe repository-relative path")


class ManifestTarget(BaseModel):
    namespace: str = Field(min_length=1)
    deployment: str = Field(min_length=1)
    container: str = Field(min_length=1)


class ResourceChange(BaseModel):
    field: str
    current: str
    recommended: str


class ManifestPatchReport(BaseModel):
    source_path: str
    document_index: int
    target: ManifestTarget
    original_sha256: str
    changes: list[ResourceChange]
    eligibility_warnings: list[str]
    recommendation_evidence: list[str]


class ManifestPatch(BaseModel):
    patched_content: str
    unified_diff: str
    report: ManifestPatchReport


@dataclass(frozen=True)
class _Candidate:
    source: ManifestSource
    document_index: int
    document: MappingNode


def generate_resource_patch(
    sources: list[ManifestSource],
    target: ManifestTarget,
    evaluation: EvaluationResult,
) -> ManifestPatch:
    if evaluation.patch_eligibility.status != "eligible":
        detail = "; ".join(evaluation.patch_eligibility.blocking_reasons)
        raise ManifestPatchError(f"patch eligibility is blocked: {detail}")
    if not sources:
        raise ManifestPatchError("at least one manifest source is required")

    candidates = [
        candidate
        for source in sources
        for candidate in _find_deployments(source, target)
    ]
    if not candidates:
        raise ManifestPatchError(
            "no apps/v1 Deployment matched "
            f"{target.namespace}/{target.deployment}"
        )
    if len(candidates) > 1:
        locations = ", ".join(
            f"{item.source.path}#document-{item.document_index}"
            for item in candidates
        )
        raise ManifestPatchError(f"manifest target is ambiguous across: {locations}")

    candidate = candidates[0]
    container = _target_container(candidate, target.container)
    value_nodes = _resource_nodes(container)
    try:
        actual = CurrentResources(
            cpu_request_millicores=parse_cpu_millicores(
                value_nodes["requests.cpu"].value
            ),
            cpu_limit_millicores=parse_cpu_millicores(value_nodes["limits.cpu"].value),
            memory_request_mib=parse_memory_mib(
                value_nodes["requests.memory"].value
            ),
            memory_limit_mib=parse_memory_mib(value_nodes["limits.memory"].value),
        )
    except (KubernetesCollectionError, ValueError) as exc:
        raise ManifestPatchError(f"manifest contains invalid resource values: {exc}") from exc
    if actual != evaluation.current:
        raise ManifestPatchError(
            "manifest resources are stale: repository values do not match the evaluated workload"
        )

    recommended = evaluation.recommendation.recommended
    replacements = {
        "requests.cpu": f"{recommended.cpu_request_millicores}m",
        "requests.memory": f"{recommended.memory_request_mib}Mi",
        "limits.cpu": f"{recommended.cpu_limit_millicores}m",
        "limits.memory": f"{recommended.memory_limit_mib}Mi",
    }
    resource_attributes = {
        "requests.cpu": "cpu_request_millicores",
        "requests.memory": "memory_request_mib",
        "limits.cpu": "cpu_limit_millicores",
        "limits.memory": "memory_limit_mib",
    }
    changed_fields = [
        field
        for field, attribute in resource_attributes.items()
        if getattr(actual, attribute) != getattr(recommended, attribute)
    ]
    changes = [
        ResourceChange(
            field=field,
            current=node.value,
            recommended=replacements[field],
        )
        for field, node in value_nodes.items()
        if field in changed_fields
    ]
    if not changes:
        raise ManifestPatchError("manifest already contains the recommended resources")

    patched = candidate.source.content
    for field, node in sorted(
        (
            (field, node)
            for field, node in value_nodes.items()
            if field in changed_fields
        ),
        key=lambda item: item[1].start_mark.index,
        reverse=True,
    ):
        rendered = _render_scalar(replacements[field], node.style)
        patched = (
            patched[: node.start_mark.index]
            + rendered
            + patched[node.end_mark.index :]
        )

    path = candidate.source.path
    diff = "".join(
        unified_diff(
            candidate.source.content.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return ManifestPatch(
        patched_content=patched,
        unified_diff=diff,
        report=ManifestPatchReport(
            source_path=path,
            document_index=candidate.document_index,
            target=target,
            original_sha256=hashlib.sha256(candidate.source.content.encode()).hexdigest(),
            changes=changes,
            eligibility_warnings=evaluation.patch_eligibility.warnings,
            recommendation_evidence=evaluation.recommendation.evidence,
        ),
    )


def _find_deployments(
    source: ManifestSource, target: ManifestTarget
) -> list[_Candidate]:
    try:
        documents = list(yaml.compose_all(source.content))
    except yaml.YAMLError as exc:
        raise ManifestPatchError(f"invalid YAML in {source.path}: {exc}") from exc

    candidates = []
    for index, document in enumerate(documents, start=1):
        if not isinstance(document, MappingNode):
            continue
        if _optional_scalar_value(document, "apiVersion") != "apps/v1":
            continue
        if _optional_scalar_value(document, "kind") != "Deployment":
            continue
        metadata = _mapping_value(document, "metadata")
        if _scalar_value(metadata, "name") != target.deployment:
            continue
        namespace = _optional_scalar_value(metadata, "namespace") or "default"
        if namespace != target.namespace:
            continue
        candidates.append(_Candidate(source, index, document))
    return candidates


def _target_container(candidate: _Candidate, container_name: str) -> MappingNode:
    containers = _sequence_value(
        _mapping_value(
            _mapping_value(
                _mapping_value(candidate.document, "spec"), "template"
            ),
            "spec",
        ),
        "containers",
    )
    matching = [
        item
        for item in containers.value
        if isinstance(item, MappingNode)
        and _scalar_value(item, "name") == container_name
    ]
    location = f"{candidate.source.path}#document-{candidate.document_index}"
    if not matching:
        raise ManifestPatchError(
            f"container {container_name!r} was not found in {location}"
        )
    if len(matching) > 1:
        raise ManifestPatchError(
            f"container {container_name!r} is duplicated in {location}"
        )
    return matching[0]


def _resource_nodes(container: MappingNode) -> dict[str, ScalarNode]:
    resources = _mapping_value(container, "resources")
    requests = _mapping_value(resources, "requests")
    limits = _mapping_value(resources, "limits")
    return {
        "requests.cpu": _local_scalar_value(requests, "cpu"),
        "requests.memory": _local_scalar_value(requests, "memory"),
        "limits.cpu": _local_scalar_value(limits, "cpu"),
        "limits.memory": _local_scalar_value(limits, "memory"),
    }


def _mapping_entries(mapping: MappingNode, key: str) -> list[tuple[Node, Node]]:
    return [
        (key_node, value_node)
        for key_node, value_node in mapping.value
        if isinstance(key_node, ScalarNode) and key_node.value == key
    ]


def _unique_entry(mapping: MappingNode, key: str) -> tuple[Node, Node]:
    entries = _mapping_entries(mapping, key)
    if not entries:
        raise ManifestPatchError(f"manifest is missing required field {key!r}")
    if len(entries) > 1:
        raise ManifestPatchError(f"manifest contains duplicate field {key!r}")
    return entries[0]


def _mapping_value(mapping: MappingNode, key: str) -> MappingNode:
    _, value = _unique_entry(mapping, key)
    if not isinstance(value, MappingNode):
        raise ManifestPatchError(f"manifest field {key!r} must be an object")
    return value


def _sequence_value(mapping: MappingNode, key: str) -> SequenceNode:
    _, value = _unique_entry(mapping, key)
    if not isinstance(value, SequenceNode):
        raise ManifestPatchError(f"manifest field {key!r} must be a list")
    return value


def _scalar_value(mapping: MappingNode, key: str) -> str:
    _, value = _unique_entry(mapping, key)
    if not isinstance(value, ScalarNode):
        raise ManifestPatchError(f"manifest field {key!r} must be a scalar")
    return value.value


def _optional_scalar_value(mapping: MappingNode, key: str) -> str | None:
    entries = _mapping_entries(mapping, key)
    if not entries:
        return None
    if len(entries) > 1:
        raise ManifestPatchError(f"manifest contains duplicate field {key!r}")
    value = entries[0][1]
    if not isinstance(value, ScalarNode):
        raise ManifestPatchError(f"manifest field {key!r} must be a scalar")
    return value.value


def _local_scalar_value(mapping: MappingNode, key: str) -> ScalarNode:
    key_node, value = _unique_entry(mapping, key)
    if not isinstance(value, ScalarNode):
        raise ManifestPatchError(f"manifest field {key!r} must be a scalar")
    if value.start_mark.line != key_node.start_mark.line:
        raise ManifestPatchError(
            f"manifest field {key!r} must use a local scalar, not an alias or block value"
        )
    if value.style not in {None, "'", '"'}:
        raise ManifestPatchError(f"manifest field {key!r} uses an unsupported scalar style")
    return value


def _render_scalar(value: str, style: str | None) -> str:
    if style == '"':
        return json.dumps(value)
    if style == "'":
        return "'" + value.replace("'", "''") + "'"
    return value
