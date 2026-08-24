import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from benchmarks.artifact import LoadedBenchmarkResult, load_benchmark_result
from benchmarks.pair import (
    CounterbalancedPairAssessment,
    assess_counterbalanced_pair,
)


class CounterbalancedPairArtifactError(RuntimeError):
    """Raised when a persisted counterbalanced pair is unsafe or inconsistent."""


class CounterbalancedPairFileMetadata(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class CounterbalancedPairIndex(BaseModel):
    schema_version: Literal[1]
    artifact_id: str = Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_ids: list[str] = Field(min_length=2, max_length=2)
    files: dict[str, CounterbalancedPairFileMetadata]


class CounterbalancedPairArtifact(BaseModel):
    artifact_id: str = Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    benchmark_ids: list[str] = Field(min_length=2, max_length=2)
    path: Path
    reused: bool
    files: list[str]


class LoadedCounterbalancedPair(BaseModel):
    artifact_id: str = Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    path: Path
    assessment: CounterbalancedPairAssessment
    before_after: LoadedBenchmarkResult
    after_before: LoadedBenchmarkResult
    report_path: Path


def write_counterbalanced_pair(
    output_root: Path,
    first_path: Path,
    second_path: Path,
) -> CounterbalancedPairArtifact:
    assessment = assess_counterbalanced_pair(first_path, second_path)
    if assessment.status != "pass" or assessment.proposal_id is None:
        raise CounterbalancedPairArtifactError(
            f"only a passing counterbalanced assessment can be persisted, got {assessment.status}"
        )
    results = [load_benchmark_result(first_path), load_benchmark_result(second_path)]
    results.sort(key=lambda result: result.artifact_id)
    payloads = _pair_payloads(assessment, results)
    content_digest = _content_digest(payloads)
    benchmark_ids = [result.artifact_id for result in results]
    index = CounterbalancedPairIndex(
        schema_version=1,
        artifact_id=assessment.assessment_id,
        proposal_id=assessment.proposal_id,
        content_digest_sha256=content_digest,
        benchmark_ids=benchmark_ids,
        files={
            path: CounterbalancedPairFileMetadata(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            for path, content in sorted(payloads.items())
        },
    )
    payloads["pair.json"] = _canonical_json(index.model_dump(mode="json"))
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final_path = output_root / assessment.assessment_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            _validate_existing(final_path, payloads)
            return CounterbalancedPairArtifact(
                artifact_id=assessment.assessment_id,
                proposal_id=assessment.proposal_id,
                benchmark_ids=benchmark_ids,
                path=final_path,
                reused=True,
                files=sorted(payloads),
            )
        staging = Path(
            tempfile.mkdtemp(prefix=f".{assessment.assessment_id}-", dir=output_root)
        )
        staging.chmod(0o700)
        for relative_path, content in sorted(payloads.items()):
            _write_file(staging, relative_path, content)
        _fsync_tree(staging)
        os.rename(staging, final_path)
        staging = None
        _fsync_directory(output_root)
        return CounterbalancedPairArtifact(
            artifact_id=assessment.assessment_id,
            proposal_id=assessment.proposal_id,
            benchmark_ids=benchmark_ids,
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


def load_counterbalanced_pair(path: Path) -> LoadedCounterbalancedPair:
    if path.is_symlink() or not path.is_dir():
        raise CounterbalancedPairArtifactError("pair path must be a regular directory")
    index_path = path / "pair.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise CounterbalancedPairArtifactError("pair artifact is missing pair.json")
    try:
        index_bytes = index_path.read_bytes()
        raw_index = json.loads(index_bytes)
        index = CounterbalancedPairIndex.model_validate(raw_index)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CounterbalancedPairArtifactError("pair index is invalid") from exc
    if index_bytes != _canonical_json(raw_index):
        raise CounterbalancedPairArtifactError("pair index is not canonical JSON")
    if path.name != index.artifact_id:
        raise CounterbalancedPairArtifactError("pair directory does not match artifact ID")
    actual_files = []
    payloads: dict[str, bytes] = {}
    for item in path.rglob("*"):
        if item.is_symlink():
            raise CounterbalancedPairArtifactError("pair artifact contains a symlink")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if set(actual_files) != set(index.files) | {"pair.json"}:
        raise CounterbalancedPairArtifactError("pair artifact file set is invalid")
    for relative_path, metadata in index.files.items():
        relative = _safe_relative_path(relative_path)
        content = path.joinpath(*relative.parts).read_bytes()
        if len(content) != metadata.size_bytes:
            raise CounterbalancedPairArtifactError(
                f"pair payload size changed: {relative_path}"
            )
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise CounterbalancedPairArtifactError(
                f"pair payload digest changed: {relative_path}"
            )
        payloads[relative_path] = content
    if _content_digest(payloads) != index.content_digest_sha256:
        raise CounterbalancedPairArtifactError("pair content digest is invalid")
    try:
        results = [
            load_benchmark_result(path / "trials" / benchmark_id)
            for benchmark_id in index.benchmark_ids
        ]
        assessment = assess_counterbalanced_pair(results[0].path, results[1].path)
        persisted = CounterbalancedPairAssessment.model_validate_json(
            payloads["assessment.json"]
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise CounterbalancedPairArtifactError(
            "embedded benchmark evidence is invalid"
        ) from exc
    if assessment != persisted or assessment.assessment_id != index.artifact_id:
        raise CounterbalancedPairArtifactError("pair assessment does not replay")
    if assessment.status != "pass" or assessment.proposal_id != index.proposal_id:
        raise CounterbalancedPairArtifactError("pair assessment is not publication-ready")
    if payloads["report.md"] != _render_report(assessment).encode():
        raise CounterbalancedPairArtifactError("pair report does not replay")
    by_order = {
        trial.measurement_order: result
        for trial, result in zip(assessment.trials, results, strict=True)
    }
    return LoadedCounterbalancedPair(
        artifact_id=index.artifact_id,
        proposal_id=index.proposal_id,
        path=path,
        assessment=assessment,
        before_after=by_order["before-after"],
        after_before=by_order["after-before"],
        report_path=path / "report.md",
    )


def _pair_payloads(
    assessment: CounterbalancedPairAssessment,
    results: list[LoadedBenchmarkResult],
) -> dict[str, bytes]:
    payloads = {
        "assessment.json": _canonical_json(assessment.model_dump(mode="json")),
        "report.md": _render_report(assessment).encode(),
    }
    for result in results:
        for item in sorted(result.path.rglob("*")):
            if item.is_file():
                relative = item.relative_to(result.path).as_posix()
                payloads[f"trials/{result.artifact_id}/{relative}"] = item.read_bytes()
    return payloads


def _render_report(assessment: CounterbalancedPairAssessment) -> str:
    lines = [
        "# KubeFit counterbalanced benchmark pair",
        "",
        f"- Pair: `{assessment.assessment_id}`",
        f"- Proposal: `{assessment.proposal_id}`",
        f"- Verdict: **{assessment.status.upper()}**",
        "",
        "## Trials",
        "",
        "| Benchmark | Order | Verdict |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| `{trial.benchmark_id}` | `{trial.measurement_order}` | {trial.verdict_status} |"
        for trial in assessment.trials
    )
    lines.extend(["", "## Checks", "", "| Code | Status | Reason |", "|---|---|---|"])
    lines.extend(
        f"| `{check.code}` | {check.status} | {check.reason.replace('|', r'\|')} |"
        for check in assessment.checks
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {warning}" for warning in assessment.warnings)
    return "\n".join(lines) + "\n"


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _content_digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative_path, content in sorted(payloads.items()):
        digest.update(relative_path.encode())
        digest.update(b"\0")
        digest.update(str(len(content)).encode())
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\n" in value or "\r" in value:
        raise CounterbalancedPairArtifactError("pair payload path is unsafe")
    return path


def _validate_output_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise CounterbalancedPairArtifactError("pair output root must be a regular directory")


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise CounterbalancedPairArtifactError("another pair publication holds the lock") from exc


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
    directories = [item for item in root.rglob("*") if item.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_existing(path: Path, expected: dict[str, bytes]) -> None:
    if path.is_symlink() or not path.is_dir():
        raise CounterbalancedPairArtifactError("existing pair path is unsafe")
    actual = [item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()]
    if sorted(actual) != sorted(expected):
        raise CounterbalancedPairArtifactError("existing pair file set changed")
    for relative_path, content in expected.items():
        if (path / relative_path).read_bytes() != content:
            raise CounterbalancedPairArtifactError(
                f"existing pair payload changed: {relative_path}"
            )
