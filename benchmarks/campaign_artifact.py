import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.campaign import (
    BenchmarkCampaignCompletion,
    BenchmarkCampaignPlan,
    assess_benchmark_campaign,
    load_benchmark_campaign_plan,
)
from benchmarks.pair_artifact import LoadedCounterbalancedPair, load_counterbalanced_pair


class BenchmarkCampaignEvidenceError(RuntimeError):
    """Raised when completed campaign evidence is unsafe or inconsistent."""


class BenchmarkCampaignEvidenceFileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class BenchmarkCampaignEvidenceIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^benchmark-campaign-evidence-[0-9a-f]{32}$")
    campaign_id: str = Field(pattern=r"^benchmark-campaign-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    pair_ids: list[str] = Field(min_length=2, max_length=100)
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, BenchmarkCampaignEvidenceFileMetadata]


class BenchmarkCampaignEvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^benchmark-campaign-evidence-[0-9a-f]{32}$")
    campaign_id: str = Field(pattern=r"^benchmark-campaign-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    pair_ids: list[str] = Field(min_length=2, max_length=100)
    path: Path
    reused: bool
    files: list[str]


class LoadedBenchmarkCampaignEvidence(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    artifact_id: str = Field(pattern=r"^benchmark-campaign-evidence-[0-9a-f]{32}$")
    campaign_id: str = Field(pattern=r"^benchmark-campaign-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    path: Path
    plan: BenchmarkCampaignPlan
    completion: BenchmarkCampaignCompletion
    pairs: list[LoadedCounterbalancedPair]
    report_path: Path


def write_benchmark_campaign_evidence(
    output_root: Path,
    plan_path: Path,
    pair_paths: list[Path],
) -> BenchmarkCampaignEvidenceArtifact:
    completion = assess_benchmark_campaign(plan_path, pair_paths)
    if completion.status != "complete":
        raise BenchmarkCampaignEvidenceError(
            f"only a complete benchmark campaign can be persisted, got {completion.status}"
        )
    plan = load_benchmark_campaign_plan(plan_path)
    pairs_by_id = {
        pair.artifact_id: pair
        for pair in (load_counterbalanced_pair(path) for path in pair_paths)
    }
    pairs = [pairs_by_id[pair_id] for pair_id in completion.pair_ids]
    payloads = _embedded_payloads(plan_path, pairs, completion)
    artifact_id = _evidence_id(plan, completion, payloads)
    payloads["report.md"] = _render_report(artifact_id, completion).encode()
    content_digest = _content_digest(payloads)
    index = BenchmarkCampaignEvidenceIndex(
        artifact_id=artifact_id,
        campaign_id=plan.campaign_id,
        proposal_id=plan.proposal_id,
        pair_ids=completion.pair_ids,
        content_digest_sha256=content_digest,
        files={
            name: BenchmarkCampaignEvidenceFileMetadata(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            for name, content in sorted(payloads.items())
        },
    )
    payloads["evidence.json"] = _canonical_json(index.model_dump(mode="json"))
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final_path = output_root / artifact_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            _validate_existing(final_path, payloads)
            return _artifact(index, final_path, True, payloads)
        staging = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=output_root))
        staging.chmod(0o700)
        for relative_path, content in sorted(payloads.items()):
            _write_file(staging, relative_path, content)
        _fsync_tree(staging)
        os.rename(staging, final_path)
        staging = None
        _fsync_directory(output_root)
        return _artifact(index, final_path, False, payloads)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
        _fsync_directory(output_root)


def load_benchmark_campaign_evidence(path: Path) -> LoadedBenchmarkCampaignEvidence:
    if path.is_symlink() or not path.is_dir():
        raise BenchmarkCampaignEvidenceError("campaign evidence path must be a regular directory")
    index_path = path / "evidence.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise BenchmarkCampaignEvidenceError("campaign evidence is missing evidence.json")
    try:
        index_bytes = index_path.read_bytes()
        raw = json.loads(index_bytes)
        index = BenchmarkCampaignEvidenceIndex.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise BenchmarkCampaignEvidenceError("campaign evidence index is invalid") from exc
    if index_bytes != _canonical_json(raw):
        raise BenchmarkCampaignEvidenceError("campaign evidence index is not canonical JSON")
    if path.name != index.artifact_id:
        raise BenchmarkCampaignEvidenceError(
            "campaign evidence directory does not match artifact ID"
        )
    actual_files = []
    payloads: dict[str, bytes] = {}
    for item in path.rglob("*"):
        if item.is_symlink():
            raise BenchmarkCampaignEvidenceError("campaign evidence contains a symlink")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if set(actual_files) != set(index.files) | {"evidence.json"}:
        raise BenchmarkCampaignEvidenceError("campaign evidence file set is invalid")
    for relative_path, metadata in index.files.items():
        relative = _safe_relative_path(relative_path)
        content = path.joinpath(*relative.parts).read_bytes()
        if len(content) != metadata.size_bytes:
            raise BenchmarkCampaignEvidenceError(
                f"campaign evidence payload size changed: {relative_path}"
            )
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise BenchmarkCampaignEvidenceError(
                f"campaign evidence payload digest changed: {relative_path}"
            )
        payloads[relative_path] = content
    if _content_digest(payloads) != index.content_digest_sha256:
        raise BenchmarkCampaignEvidenceError("campaign evidence content digest is invalid")
    try:
        embedded_plan_path = path / "campaign" / index.campaign_id
        plan = load_benchmark_campaign_plan(embedded_plan_path)
        pairs = [
            load_counterbalanced_pair(path / "pairs" / pair_id)
            for pair_id in index.pair_ids
        ]
        completion = assess_benchmark_campaign(
            embedded_plan_path, [pair.path for pair in pairs]
        )
        persisted = BenchmarkCampaignCompletion.model_validate_json(
            payloads["completion.json"]
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise BenchmarkCampaignEvidenceError(
            "embedded campaign evidence is invalid"
        ) from exc
    if completion != persisted or completion.status != "complete":
        raise BenchmarkCampaignEvidenceError("campaign completion does not replay")
    if (
        plan.campaign_id != index.campaign_id
        or plan.proposal_id != index.proposal_id
        or completion.pair_ids != index.pair_ids
    ):
        raise BenchmarkCampaignEvidenceError("campaign evidence identity binding is invalid")
    identity_payloads = {
        name: content for name, content in payloads.items() if name != "report.md"
    }
    expected_id = _evidence_id(plan, completion, identity_payloads)
    if expected_id != index.artifact_id:
        raise BenchmarkCampaignEvidenceError("campaign evidence ID does not replay")
    if payloads["report.md"] != _render_report(index.artifact_id, completion).encode():
        raise BenchmarkCampaignEvidenceError("campaign evidence report does not replay")
    return LoadedBenchmarkCampaignEvidence(
        artifact_id=index.artifact_id,
        campaign_id=index.campaign_id,
        proposal_id=index.proposal_id,
        path=path,
        plan=plan,
        completion=completion,
        pairs=pairs,
        report_path=path / "report.md",
    )


def _embedded_payloads(
    plan_path: Path,
    pairs: list[LoadedCounterbalancedPair],
    completion: BenchmarkCampaignCompletion,
) -> dict[str, bytes]:
    payloads = {
        "completion.json": _canonical_json(completion.model_dump(mode="json")),
    }
    for item in sorted(plan_path.rglob("*")):
        if item.is_file():
            payloads[
                f"campaign/{plan_path.name}/{item.relative_to(plan_path).as_posix()}"
            ] = item.read_bytes()
    for pair in pairs:
        for item in sorted(pair.path.rglob("*")):
            if item.is_file():
                relative = item.relative_to(pair.path).as_posix()
                payloads[f"pairs/{pair.artifact_id}/{relative}"] = item.read_bytes()
    return payloads


def _evidence_id(
    plan: BenchmarkCampaignPlan,
    completion: BenchmarkCampaignCompletion,
    payloads: dict[str, bytes],
) -> str:
    identity = {
        "schema_version": 1,
        "campaign_id": plan.campaign_id,
        "proposal_id": plan.proposal_id,
        "pair_ids": completion.pair_ids,
        "files": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in sorted(payloads.items())
            if name != "report.md"
        },
    }
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return f"benchmark-campaign-evidence-{digest[:32]}"


def _render_report(
    artifact_id: str,
    completion: BenchmarkCampaignCompletion,
) -> str:
    lines = [
        "# KubeFit completed benchmark campaign evidence",
        "",
        f"- Evidence: `{artifact_id}`",
        f"- Campaign: `{completion.campaign_id}`",
        f"- Proposal: `{completion.proposal_id}`",
        f"- Status: **{completion.status.upper()}**",
        f"- Completed pairs: `{completion.completed_pairs}/{completion.planned_pairs}`",
        "",
        "## Chronological pairs",
        "",
    ]
    lines.extend(
        f"{index}. `{pair_id}`" for index, pair_id in enumerate(completion.pair_ids, start=1)
    )
    lines.extend(["", "## Checks", "", "| Code | Status | Reason |", "|---|---|---|"])
    lines.extend(
        f"| `{check.code}` | {check.status} | {check.reason.replace('|', r'\|')} |"
        for check in completion.checks
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in completion.limitations)
    return "\n".join(lines) + "\n"


def _artifact(index, path, reused, payloads) -> BenchmarkCampaignEvidenceArtifact:
    return BenchmarkCampaignEvidenceArtifact(
        artifact_id=index.artifact_id,
        campaign_id=index.campaign_id,
        proposal_id=index.proposal_id,
        pair_ids=index.pair_ids,
        path=path,
        reused=reused,
        files=sorted(payloads),
    )


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _content_digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(payloads.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(str(len(content)).encode())
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\n" in value or "\r" in value:
        raise BenchmarkCampaignEvidenceError("campaign evidence payload path is unsafe")
    return path


def _validate_output_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise BenchmarkCampaignEvidenceError(
            "campaign evidence output root must be a regular directory"
        )


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BenchmarkCampaignEvidenceError(
            "another campaign evidence publication holds the lock"
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
        raise BenchmarkCampaignEvidenceError("existing campaign evidence path is unsafe")
    actual = [item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()]
    if sorted(actual) != sorted(expected) or any(item.is_symlink() for item in path.rglob("*")):
        raise BenchmarkCampaignEvidenceError("existing campaign evidence file set changed")
    for relative_path, content in expected.items():
        if (path / relative_path).read_bytes() != content:
            raise BenchmarkCampaignEvidenceError(
                f"existing campaign evidence payload changed: {relative_path}"
            )
