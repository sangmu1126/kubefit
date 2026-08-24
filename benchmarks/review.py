import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from benchmarks.artifact import (
    BENCHMARK_RESULT_PAYLOAD_PATHS,
    BenchmarkResultArtifactError,
    BenchmarkResultIndex,
    load_benchmark_result,
)
from benchmarks.result import BenchmarkMeasurement, BenchmarkVerdict, compare_benchmarks

MAX_REVIEW_FILE_BYTES = 128 * 1024


class BenchmarkReviewRequest(BaseModel):
    result_json: str = Field(max_length=MAX_REVIEW_FILE_BYTES)
    before_json: str = Field(max_length=MAX_REVIEW_FILE_BYTES)
    after_json: str = Field(max_length=MAX_REVIEW_FILE_BYTES)
    verdict_json: str = Field(max_length=MAX_REVIEW_FILE_BYTES)


class BenchmarkReviewCheck(BaseModel):
    code: Literal[
        "index_identity",
        "selected_payload_hashes",
        "proposal_binding",
        "raw_evidence_binding",
        "verdict_replay",
        "complete_artifact_integrity",
    ]
    status: Literal["pass"] = "pass"
    reason: str


class BenchmarkReview(BaseModel):
    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^benchmark-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    verification_level: Literal[
        "index_bound_replay", "full_artifact_replay"
    ] = "index_bound_replay"
    before: BenchmarkMeasurement
    after: BenchmarkMeasurement
    verdict: BenchmarkVerdict
    checks: list[BenchmarkReviewCheck]
    limitations: list[str]


def review_benchmark_result(request: BenchmarkReviewRequest) -> BenchmarkReview:
    encoded_payloads = {
        "result.json": request.result_json.encode(),
        "measurements/before.json": request.before_json.encode(),
        "measurements/after.json": request.after_json.encode(),
        "verdict.json": request.verdict_json.encode(),
    }
    for path, payload in encoded_payloads.items():
        if len(payload) > MAX_REVIEW_FILE_BYTES:
            raise ValueError(f"benchmark review payload exceeds 128 KiB: {path}")
    index_bytes = encoded_payloads["result.json"]
    try:
        raw_index = json.loads(index_bytes)
        index = BenchmarkResultIndex.model_validate(raw_index)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("benchmark result index is invalid") from exc
    if index_bytes != _canonical_json(raw_index):
        raise ValueError("benchmark result index is not canonical JSON")
    if index.content_digest_sha256[:32] != index.artifact_id.removeprefix("benchmark-"):
        raise ValueError("benchmark artifact ID does not match its declared content digest")
    if set(index.files) != BENCHMARK_RESULT_PAYLOAD_PATHS:
        raise ValueError("benchmark result index does not contain the expected payload set")

    selected_payloads = {
        path: encoded_payloads[path]
        for path in (
            "measurements/before.json",
            "measurements/after.json",
            "verdict.json",
        )
    }
    for path, payload in selected_payloads.items():
        metadata = index.files[path]
        if len(payload) != metadata.size_bytes:
            raise ValueError(f"benchmark payload size conflicts with index: {path}")
        if hashlib.sha256(payload).hexdigest() != metadata.sha256:
            raise ValueError(f"benchmark payload digest conflicts with index: {path}")

    try:
        before = BenchmarkMeasurement.model_validate_json(request.before_json)
        after = BenchmarkMeasurement.model_validate_json(request.after_json)
        verdict = BenchmarkVerdict.model_validate_json(request.verdict_json)
    except ValueError as exc:
        raise ValueError("benchmark measurements or verdict are invalid") from exc

    if before.proposal_id != index.proposal_id or after.proposal_id != index.proposal_id:
        raise ValueError("benchmark measurements do not match the indexed proposal")
    if before.variant != "before" or after.variant != "after":
        raise ValueError("benchmark measurements are not ordered as before then after")

    evidence_bindings = (
        (before.provenance.k6_summary_sha256, "evidence/k6/before-summary.json"),
        (before.provenance.k6_raw_sha256, "evidence/k6/before-raw.json"),
        (after.provenance.k6_summary_sha256, "evidence/k6/after-summary.json"),
        (after.provenance.k6_raw_sha256, "evidence/k6/after-raw.json"),
    )
    for declared_sha, path in evidence_bindings:
        if declared_sha != index.files[path].sha256:
            raise ValueError(f"benchmark measurement evidence conflicts with index: {path}")

    expected_verdict = compare_benchmarks(before, after)
    if verdict != expected_verdict:
        raise ValueError("benchmark verdict conflicts with replayed measurements")

    return BenchmarkReview(
        artifact_id=index.artifact_id,
        proposal_id=index.proposal_id,
        before=before,
        after=after,
        verdict=verdict,
        checks=[
            BenchmarkReviewCheck(
                code="index_identity",
                reason="artifact ID matches the content digest declared by the canonical index",
            ),
            BenchmarkReviewCheck(
                code="selected_payload_hashes",
                reason=(
                    "before, after, and verdict payload bytes match indexed sizes and "
                    "SHA-256 digests"
                ),
            ),
            BenchmarkReviewCheck(
                code="proposal_binding",
                reason="both measurements reference the proposal declared by the result index",
            ),
            BenchmarkReviewCheck(
                code="raw_evidence_binding",
                reason=(
                    "measurement provenance digests match indexed k6 summary and raw "
                    "evidence digests"
                ),
            ),
            BenchmarkReviewCheck(
                code="verdict_replay",
                reason="verdict was recomputed from the indexed before and after measurements",
            ),
        ],
        limitations=[
            (
                "k6 raw bytes, k6 summaries, and report.md were not uploaded; their bytes and the "
                "complete artifact content digest were not recomputed"
            ),
            (
                "the fixed approximately 160-second load per variant does not establish "
                "representative production traffic"
            ),
            (
                "controlled-demo observation provenance is not encoded in the benchmark bundle; "
                "inspect the proposal analysis artifact separately"
            ),
        ],
    )


def review_full_benchmark_result(path: Path) -> BenchmarkReview:
    """Fully verify a local result bundle before returning its review projection."""
    try:
        loaded = load_benchmark_result(path)
    except (BenchmarkResultArtifactError, OSError) as exc:
        raise ValueError("complete benchmark result is invalid") from exc
    return BenchmarkReview(
        artifact_id=loaded.artifact_id,
        proposal_id=loaded.proposal_id,
        verification_level="full_artifact_replay",
        before=loaded.before,
        after=loaded.after,
        verdict=loaded.verdict,
        checks=[
            BenchmarkReviewCheck(
                code="complete_artifact_integrity",
                reason=(
                    "the exact file set, every payload size and SHA-256 digest, the "
                    "aggregate content digest, raw k6 evidence, and report were revalidated"
                ),
            ),
            BenchmarkReviewCheck(
                code="verdict_replay",
                reason="verdict was recomputed from the fully verified measurements",
            ),
        ],
        limitations=[
            (
                "the fixed approximately 160-second load per variant does not establish "
                "representative production traffic"
            ),
            (
                "controlled-demo observation provenance is not encoded in the benchmark bundle; "
                "inspect the proposal analysis artifact separately"
            ),
        ],
    )


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
