import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from evaluator import AnalysisArtifact, EvaluationResult, compare_request_costs
from gitops.manifest import ManifestPatch, ManifestPatchReport, ManifestTarget
from recommender import CurrentResources
from recommender.models import ResourceValues


class ProposalBundleError(RuntimeError):
    """Raised when an immutable proposal bundle cannot be safely published."""


class BenchmarkContext(BaseModel):
    schema_version: Literal[1] = 1
    target: ManifestTarget
    before_resources: CurrentResources
    after_resources: ResourceValues
    required_metrics: list[str]
    eligibility_warnings: list[str]


class ProposalBundle(BaseModel):
    artifact_id: str
    path: Path
    reused: bool
    files: list[str]


class LoadedProposalBundle(BaseModel):
    artifact_id: str
    path: Path
    source_path: str
    target: ManifestTarget
    before_manifest: Path
    after_manifest: Path
    before_request_cost_usd: Decimal = Field(gt=0)
    after_request_cost_usd: Decimal = Field(gt=0)
    workload_uid: str | None = None
    workload_created_at: datetime | None = None


class _FileMetadata(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class _ArtifactIndex(BaseModel):
    schema_version: Literal[1]
    artifact_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str
    files: dict[str, _FileMetadata]


def write_proposal_bundle(
    output_root: Path,
    patch: ManifestPatch,
    evaluation: EvaluationResult,
    analysis: AnalysisArtifact | None = None,
) -> ProposalBundle:
    source_path = _safe_relative_path(patch.report.source_path)
    if analysis is not None:
        if analysis.evaluation != evaluation:
            raise ProposalBundleError("analysis evaluation conflicts with proposal evaluation")
        if analysis.target.model_dump() != patch.report.target.model_dump():
            raise ProposalBundleError("analysis target conflicts with proposal target")
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    payloads = _payloads(patch, evaluation, source_path, analysis)
    content_digest = _content_digest(payloads)
    artifact_id = f"proposal-{content_digest[:32]}"
    index = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "content_digest_sha256": content_digest,
        "source_path": source_path.as_posix(),
        "files": {
            path: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
            for path, content in sorted(payloads.items())
        },
    }
    payloads["artifact.json"] = _canonical_json(index)
    final_path = output_root / artifact_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            _validate_existing_bundle(final_path, payloads)
            return ProposalBundle(
                artifact_id=artifact_id,
                path=final_path,
                reused=True,
                files=sorted(payloads),
            )

        staging = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=output_root))
        staging.chmod(0o700)
        for relative_path, content in sorted(payloads.items()):
            _write_file(staging, relative_path, content)
        _fsync_tree(staging)
        os.rename(staging, final_path)
        staging = None
        _fsync_directory(output_root)
        return ProposalBundle(
            artifact_id=artifact_id,
            path=final_path,
            reused=False,
            files=sorted(payloads),
        )
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
        _fsync_directory(output_root)


def load_proposal_bundle(path: Path) -> LoadedProposalBundle:
    """Load and cryptographically revalidate a published proposal bundle."""
    if path.is_symlink() or not path.is_dir():
        raise ProposalBundleError(f"proposal path is not a safe directory: {path}")
    artifact_path = path / "artifact.json"
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise ProposalBundleError("proposal is missing a regular artifact.json")
    try:
        artifact_bytes = artifact_path.read_bytes()
        raw_index = json.loads(artifact_bytes)
        index = _ArtifactIndex.model_validate(raw_index)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ProposalBundleError("proposal artifact index is invalid") from exc
    if artifact_bytes != _canonical_json(raw_index):
        raise ProposalBundleError("proposal artifact index is not canonical JSON")
    if path.name != index.artifact_id:
        raise ProposalBundleError("proposal directory name does not match artifact ID")
    if index.content_digest_sha256[:32] != index.artifact_id.removeprefix("proposal-"):
        raise ProposalBundleError("proposal artifact ID does not match its content digest")

    source_path = _safe_relative_path(index.source_path)
    expected_payload_paths = {
        "evaluation.json",
        "patch.diff",
        "patch-report.json",
        "benchmark-context.json",
        f"manifests/before/{source_path.as_posix()}",
        f"manifests/after/{source_path.as_posix()}",
    }
    if "analysis.json" in index.files:
        expected_payload_paths.add("analysis.json")
    if set(index.files) != expected_payload_paths:
        raise ProposalBundleError("proposal index does not contain the expected payload set")

    actual_files = []
    payloads: dict[str, bytes] = {}
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ProposalBundleError(f"proposal contains a symlink: {item}")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if set(actual_files) != expected_payload_paths | {"artifact.json"}:
        raise ProposalBundleError("proposal file set does not match its index")
    for relative_path, metadata in index.files.items():
        safe_path = _safe_relative_path(relative_path)
        content = path.joinpath(*safe_path.parts).read_bytes()
        if len(content) != metadata.size_bytes:
            raise ProposalBundleError(f"proposal payload size changed: {relative_path}")
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise ProposalBundleError(f"proposal payload digest changed: {relative_path}")
        payloads[relative_path] = content
    if _content_digest(payloads) != index.content_digest_sha256:
        raise ProposalBundleError("proposal content digest does not match its payloads")

    try:
        context = BenchmarkContext.model_validate_json(payloads["benchmark-context.json"])
        report = ManifestPatchReport.model_validate_json(payloads["patch-report.json"])
        evaluation = EvaluationResult.model_validate_json(payloads["evaluation.json"])
    except ValueError as exc:
        raise ProposalBundleError("proposal context or patch report is invalid") from exc
    if report.source_path != source_path.as_posix():
        raise ProposalBundleError("proposal source path conflicts with its patch report")
    if report.target != context.target:
        raise ProposalBundleError("proposal target conflicts with its patch report")
    if evaluation.current != context.before_resources:
        raise ProposalBundleError("proposal current resources conflict with its context")
    if evaluation.recommendation.recommended != context.after_resources:
        raise ProposalBundleError("proposal recommended resources conflict with its context")
    expected_cost = compare_request_costs(
        evaluation.current,
        evaluation.recommendation.recommended,
        evaluation.cost.assumptions,
        evaluation.cost.replica_count,
    )
    if evaluation.cost != expected_cost:
        raise ProposalBundleError("proposal request cost conflicts with its resources")
    analysis: AnalysisArtifact | None = None
    if "analysis.json" in payloads:
        try:
            analysis = AnalysisArtifact.model_validate_json(payloads["analysis.json"])
        except ValueError as exc:
            raise ProposalBundleError("proposal analysis identity is invalid") from exc
        if analysis.evaluation != evaluation:
            raise ProposalBundleError("proposal analysis conflicts with its evaluation")
        if analysis.target.model_dump() != context.target.model_dump():
            raise ProposalBundleError("proposal analysis conflicts with its target")

    return LoadedProposalBundle(
        artifact_id=index.artifact_id,
        path=path,
        source_path=source_path.as_posix(),
        target=context.target,
        before_manifest=path / "manifests" / "before" / source_path,
        after_manifest=path / "manifests" / "after" / source_path,
        before_request_cost_usd=evaluation.cost.current.total_usd,
        after_request_cost_usd=evaluation.cost.recommended.total_usd,
        workload_uid=analysis.workload_uid if analysis is not None else None,
        workload_created_at=(analysis.workload_created_at if analysis is not None else None),
    )


def _payloads(
    patch: ManifestPatch,
    evaluation: EvaluationResult,
    source_path: PurePosixPath,
    analysis: AnalysisArtifact | None,
) -> dict[str, bytes]:
    context = BenchmarkContext(
        target=patch.report.target.model_dump(),
        before_resources=evaluation.current,
        after_resources=evaluation.recommendation.recommended,
        required_metrics=[
            "request_cost_usd",
            "latency_p95_ms",
            "latency_p99_ms",
            "cpu_throttling_p95_percent",
            "oom_killed_count",
            "restart_count",
            "error_rate",
            "traffic_spike_recovery_seconds",
            "traffic_spike_recovered",
            "run_started_at",
            "run_finished_at",
            "k6_summary_sha256",
            "k6_raw_sha256",
        ],
        eligibility_warnings=evaluation.patch_eligibility.warnings,
    )
    relative = source_path.as_posix()
    payloads = {
        "evaluation.json": _canonical_json(evaluation.model_dump(mode="json")),
        "patch.diff": patch.unified_diff.encode(),
        "patch-report.json": _canonical_json(patch.report.model_dump(mode="json")),
        "benchmark-context.json": _canonical_json(context.model_dump(mode="json")),
        f"manifests/before/{relative}": patch.original_content.encode(),
        f"manifests/after/{relative}": patch.patched_content.encode(),
    }
    if analysis is not None:
        payloads["analysis.json"] = _canonical_json(analysis.model_dump(mode="json"))
    return payloads


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _content_digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(payloads.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(str(len(content)).encode())
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _validate_output_root(output_root: Path) -> None:
    if output_root.is_symlink():
        raise ProposalBundleError("proposal output root must not be a symlink")
    if output_root.exists() and not output_root.is_dir():
        raise ProposalBundleError("proposal output root must be a directory")


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or "\n" in value
        or "\r" in value
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ProposalBundleError("manifest path must be repository-relative")
    return path


def _acquire_lock(lock_path: Path) -> int:
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ProposalBundleError(f"another proposal publication holds {lock_path.name}") from exc


def _write_file(root: Path, relative_path: str, content: bytes) -> None:
    relative = _safe_relative_path(relative_path)
    destination = root.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with destination.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    destination.chmod(0o600)


def _fsync_tree(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_existing_bundle(path: Path, expected: dict[str, bytes]) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ProposalBundleError(f"existing proposal path is not a safe directory: {path}")
    actual_files = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ProposalBundleError(f"existing proposal contains a symlink: {item}")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if sorted(actual_files) != sorted(expected):
        raise ProposalBundleError("existing proposal file set does not match expected content")
    for relative_path, content in expected.items():
        if (path / relative_path).read_bytes() != content:
            raise ProposalBundleError(f"existing proposal file was modified: {relative_path}")
