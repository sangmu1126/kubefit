import json
from pathlib import Path

import pytest

from benchmarks import (
    BenchmarkReviewRequest,
    review_benchmark_result,
    review_full_benchmark_result,
    write_benchmark_result,
)
from tests.test_benchmark_artifact import completed_run


def review_request(result_path: Path) -> BenchmarkReviewRequest:
    return BenchmarkReviewRequest(
        result_json=(result_path / "result.json").read_text(),
        before_json=(result_path / "measurements/before.json").read_text(),
        after_json=(result_path / "measurements/after.json").read_text(),
        verdict_json=(result_path / "verdict.json").read_text(),
    )


def test_reviews_index_bound_measurements_and_replays_verdict(tmp_path: Path) -> None:
    proposal, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)

    review = review_benchmark_result(review_request(published.path))

    assert review.artifact_id == published.artifact_id
    assert review.proposal_id == proposal.artifact_id
    assert review.verification_level == "index_bound_replay"
    assert review.before == run.before
    assert review.after == run.after
    assert review.verdict == run.verdict
    assert [check.code for check in review.checks] == [
        "index_identity",
        "selected_payload_hashes",
        "proposal_binding",
        "raw_evidence_binding",
        "verdict_replay",
    ]
    assert "complete artifact content digest were not recomputed" in review.limitations[0]


def test_rejects_selected_payload_that_does_not_match_index(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)
    request = review_request(published.path)
    before = json.loads(request.before_json)
    before["steady"]["latency_p95_ms"] += 1
    request.before_json = json.dumps(before)

    with pytest.raises(ValueError, match="before.json"):
        review_benchmark_result(request)


def test_rejects_indexed_raw_evidence_digest_not_bound_by_measurement(
    tmp_path: Path,
) -> None:
    _, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)
    request = review_request(published.path)
    index = json.loads(request.result_json)
    index["files"]["evidence/k6/before-raw.json"]["sha256"] = "0" * 64
    request.result_json = json.dumps(
        index, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"

    with pytest.raises(ValueError, match="before-raw.json"):
        review_benchmark_result(request)


def test_enforces_review_limit_on_encoded_bytes() -> None:
    request = BenchmarkReviewRequest(
        result_json="한" * 50_000,
        before_json="{}",
        after_json="{}",
        verdict_json="{}",
    )

    with pytest.raises(ValueError, match="exceeds 128 KiB: result.json"):
        review_benchmark_result(request)


def test_reviews_complete_local_bundle_with_stronger_verification(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)

    review = review_full_benchmark_result(published.path)

    assert review.verification_level == "full_artifact_replay"
    assert [check.code for check in review.checks] == [
        "complete_artifact_integrity",
        "verdict_replay",
    ]
    assert "complete artifact content digest" not in " ".join(review.limitations)


def test_full_review_rejects_tampered_omitted_payload(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)
    (published.path / "report.md").write_text("tampered\n")

    with pytest.raises(ValueError, match="complete benchmark result is invalid"):
        review_full_benchmark_result(published.path)
