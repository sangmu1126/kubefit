import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from evaluator import EvaluationResult
from gitops.manifest import ManifestPatch
from recommender import CurrentResources
from recommender.models import ResourceValues


class ProposalBundleError(RuntimeError):
    """Raised when an immutable proposal bundle cannot be safely published."""


class BenchmarkContext(BaseModel):
    schema_version: int = 1
    target: dict[str, str]
    before_resources: CurrentResources
    after_resources: ResourceValues
    required_metrics: list[str]
    eligibility_warnings: list[str]


class ProposalBundle(BaseModel):
    artifact_id: str
    path: Path
    reused: bool
    files: list[str]


def write_proposal_bundle(
    output_root: Path,
    patch: ManifestPatch,
    evaluation: EvaluationResult,
) -> ProposalBundle:
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_path = _safe_relative_path(patch.report.source_path)
    payloads = _payloads(patch, evaluation, source_path)
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

        staging = Path(
            tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=output_root)
        )
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


def _payloads(
    patch: ManifestPatch,
    evaluation: EvaluationResult,
    source_path: PurePosixPath,
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
            "error_rate",
            "traffic_spike_recovery_seconds",
        ],
        eligibility_warnings=evaluation.patch_eligibility.warnings,
    )
    relative = source_path.as_posix()
    return {
        "evaluation.json": _canonical_json(evaluation.model_dump(mode="json")),
        "patch.diff": patch.unified_diff.encode(),
        "patch-report.json": _canonical_json(patch.report.model_dump(mode="json")),
        "benchmark-context.json": _canonical_json(context.model_dump(mode="json")),
        f"manifests/before/{relative}": patch.original_content.encode(),
        f"manifests/after/{relative}": patch.patched_content.encode(),
    }


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
        raise ProposalBundleError(
            f"another proposal publication holds {lock_path.name}"
        ) from exc


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
            raise ProposalBundleError(
                f"existing proposal file was modified: {relative_path}"
            )
