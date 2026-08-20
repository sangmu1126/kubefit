import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from benchmarks.measurement import CollectedMeasurement
from benchmarks.result import compare_benchmarks
from benchmarks.runner import BenchmarkRun


class BenchmarkResultArtifactError(RuntimeError):
    """Raised when a benchmark result cannot be published immutably."""


class BenchmarkResultArtifact(BaseModel):
    artifact_id: str = Field(pattern=r"^benchmark-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    path: Path
    reused: bool
    files: list[str]


class _ResultFileMetadata(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class _ResultIndex(BaseModel):
    schema_version: Literal[1]
    artifact_id: str = Field(pattern=r"^benchmark-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, _ResultFileMetadata]


def write_benchmark_result(
    output_root: Path,
    run: BenchmarkRun,
) -> BenchmarkResultArtifact:
    _validate_run(run)
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    payloads = _result_payloads(run)
    content_digest = _content_digest(payloads)
    artifact_id = f"benchmark-{content_digest[:32]}"
    index = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "proposal_id": run.proposal_id,
        "content_digest_sha256": content_digest,
        "files": {
            path: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
            for path, content in sorted(payloads.items())
        },
    }
    validated_index = _ResultIndex.model_validate(index)
    payloads["result.json"] = _canonical_json(validated_index.model_dump(mode="json"))
    final_path = output_root / artifact_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            _validate_existing_result(final_path, payloads)
            return BenchmarkResultArtifact(
                artifact_id=artifact_id,
                proposal_id=run.proposal_id,
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
        return BenchmarkResultArtifact(
            artifact_id=artifact_id,
            proposal_id=run.proposal_id,
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


def _validate_run(run: BenchmarkRun) -> None:
    if run.restored is not True:
        raise BenchmarkResultArtifactError(
            "benchmark result cannot be published before workload restoration"
        )
    if run.before.proposal_id != run.proposal_id or run.after.proposal_id != run.proposal_id:
        raise BenchmarkResultArtifactError(
            "benchmark measurements do not reference the execution proposal"
        )
    if run.before.variant != "before" or run.after.variant != "after":
        raise BenchmarkResultArtifactError("benchmark variants are not before then after")
    try:
        CollectedMeasurement(
            measurement=run.before,
            k6_summary=run.before_k6_summary,
            k6_raw=run.before_k6_raw,
        )
        CollectedMeasurement(
            measurement=run.after,
            k6_summary=run.after_k6_summary,
            k6_raw=run.after_k6_raw,
        )
    except ValueError as exc:
        raise BenchmarkResultArtifactError(
            "benchmark raw evidence conflicts with measurements"
        ) from exc
    expected_verdict = compare_benchmarks(run.before, run.after)
    if run.verdict != expected_verdict:
        raise BenchmarkResultArtifactError("benchmark verdict conflicts with measurements")


def _result_payloads(run: BenchmarkRun) -> dict[str, bytes]:
    return {
        "measurements/before.json": _canonical_json(run.before.model_dump(mode="json")),
        "measurements/after.json": _canonical_json(run.after.model_dump(mode="json")),
        "evidence/k6/before-summary.json": run.before_k6_summary,
        "evidence/k6/before-raw.json": run.before_k6_raw,
        "evidence/k6/after-summary.json": run.after_k6_summary,
        "evidence/k6/after-raw.json": run.after_k6_raw,
        "verdict.json": _canonical_json(run.verdict.model_dump(mode="json")),
        "report.md": _render_report(run).encode(),
    }


def _render_report(run: BenchmarkRun) -> str:
    before = run.before
    after = run.after
    lines = [
        "# KubeFit benchmark result",
        "",
        f"- Proposal: `{run.proposal_id}`",
        f"- Verdict: **{run.verdict.status.upper()}**",
        f"- Cost change: `{run.verdict.cost_change_percent}%`",
        "- Workload restored: `true`",
        "",
        "## Before and after",
        "",
        "| Signal | Before | After |",
        "|---|---:|---:|",
        _report_row("Monthly request cost (USD)", before.request_cost_usd, after.request_cost_usd),
        _report_row(
            "Steady latency P95 (ms)",
            before.steady.latency_p95_ms,
            after.steady.latency_p95_ms,
        ),
        _report_row(
            "Steady latency P99 (ms)",
            before.steady.latency_p99_ms,
            after.steady.latency_p99_ms,
        ),
        _report_row(
            "Spike latency P95 (ms)",
            before.spike.latency_p95_ms,
            after.spike.latency_p95_ms,
        ),
        _report_row(
            "Spike latency P99 (ms)",
            before.spike.latency_p99_ms,
            after.spike.latency_p99_ms,
        ),
        _report_row(
            "CPU throttling P95 (%)",
            before.runtime.cpu_throttling_p95_percent,
            after.runtime.cpu_throttling_p95_percent,
        ),
        _report_row(
            "OOMKilled during run",
            before.runtime.oom_killed_count,
            after.runtime.oom_killed_count,
        ),
        _report_row(
            "Restarts during run",
            before.runtime.restart_count,
            after.runtime.restart_count,
        ),
        _report_row(
            "Recovery time (s)",
            before.runtime.traffic_spike_recovery_seconds,
            after.runtime.traffic_spike_recovery_seconds,
        ),
        "",
        "## Checks",
        "",
        "| Code | Status | Reason |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| `{check.code}` | {check.status} | {_markdown_cell(check.reason)} |"
        for check in run.verdict.checks
    )
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def _report_row(label: str, before: object, after: object) -> str:
    return f"| {label} | {before} | {after} |"


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
        raise BenchmarkResultArtifactError("result output root must not be a symlink")
    if output_root.exists() and not output_root.is_dir():
        raise BenchmarkResultArtifactError("result output root must be a directory")


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or not path.parts or path.is_absolute() or ".." in path.parts:
        raise BenchmarkResultArtifactError("result path must be artifact-relative")
    return path


def _acquire_lock(lock_path: Path) -> int:
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BenchmarkResultArtifactError(
            f"another result publication holds {lock_path.name}"
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


def _validate_existing_result(path: Path, expected: dict[str, bytes]) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BenchmarkResultArtifactError(f"existing result path is not a safe directory: {path}")
    actual_files = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise BenchmarkResultArtifactError(f"existing result contains a symlink: {item}")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if sorted(actual_files) != sorted(expected):
        raise BenchmarkResultArtifactError(
            "existing result file set does not match expected content"
        )
    for relative_path, content in expected.items():
        if (path / relative_path).read_bytes() != content:
            raise BenchmarkResultArtifactError(f"existing result was modified: {relative_path}")
