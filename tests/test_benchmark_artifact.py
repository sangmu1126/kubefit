import hashlib
import json
import stat
from pathlib import Path

import pytest

import benchmarks.artifact as artifact_module
from benchmarks import (
    BenchmarkResultArtifactError,
    execute_benchmark,
    load_benchmark_result,
    write_benchmark_result,
)
from tests.test_benchmark_runner import (
    RecordingController,
    collector,
    published_proposal,
)


def completed_run(tmp_path: Path):
    proposal = published_proposal(tmp_path)
    controller = RecordingController()
    run = execute_benchmark(proposal.path, controller, collector(controller.events))
    return proposal, run


def test_publishes_complete_hashed_result_without_changing_proposal(
    tmp_path: Path,
) -> None:
    proposal, run = completed_run(tmp_path)
    proposal_files_before = sorted(
        (path.relative_to(proposal.path).as_posix(), path.read_bytes())
        for path in proposal.path.rglob("*")
        if path.is_file()
    )

    result = write_benchmark_result(tmp_path / "results", run)

    assert result.reused is False
    assert result.artifact_id.startswith("benchmark-")
    assert result.proposal_id == proposal.artifact_id
    assert len(result.files) == 9
    assert (result.path / "evidence/k6/before-raw.json").read_bytes() == b"raw:before"
    assert (result.path / "evidence/k6/after-raw.json").read_bytes() == b"raw:after"
    assert "Workload restored: `true`" in (result.path / "report.md").read_text()
    assert "## Checks" in (result.path / "report.md").read_text()
    index = json.loads((result.path / "result.json").read_text())
    assert index["proposal_id"] == proposal.artifact_id
    assert set(index["files"]) == set(result.files) - {"result.json"}
    for relative_path, metadata in index["files"].items():
        content = (result.path / relative_path).read_bytes()
        assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
        assert metadata["size_bytes"] == len(content)
        assert stat.S_IMODE((result.path / relative_path).stat().st_mode) == 0o600
    proposal_files_after = sorted(
        (path.relative_to(proposal.path).as_posix(), path.read_bytes())
        for path in proposal.path.rglob("*")
        if path.is_file()
    )
    assert proposal_files_after == proposal_files_before


def test_loads_and_semantically_revalidates_result(tmp_path: Path) -> None:
    proposal, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)

    loaded = load_benchmark_result(published.path)

    assert loaded.artifact_id == published.artifact_id
    assert loaded.proposal_id == proposal.artifact_id
    assert loaded.before == run.before
    assert loaded.after == run.after
    assert loaded.verdict == run.verdict
    assert loaded.report_path.read_text().startswith("# KubeFit benchmark result\n")


def test_reuses_identical_result_across_retries(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    root = tmp_path / "results"

    first = write_benchmark_result(root, run)
    second = write_benchmark_result(root, run)

    assert second.artifact_id == first.artifact_id
    assert second.reused is True


def test_result_id_is_stable_across_output_roots(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)

    first = write_benchmark_result(tmp_path / "first", run)
    second = write_benchmark_result(tmp_path / "second", run)

    assert second.artifact_id == first.artifact_id


@pytest.mark.parametrize("extra", [False, True])
def test_rejects_modified_or_extended_existing_result(
    tmp_path: Path, extra: bool
) -> None:
    _, run = completed_run(tmp_path)
    root = tmp_path / "results"
    first = write_benchmark_result(root, run)
    if extra:
        (first.path / "unexpected.txt").write_text("unexpected")
        message = "file set"
    else:
        (first.path / "verdict.json").write_text("tampered\n")
        message = "modified"

    with pytest.raises(BenchmarkResultArtifactError, match=message):
        write_benchmark_result(root, run)


def test_rejects_active_result_publication_lock(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    root = tmp_path / "results"
    root.mkdir()
    (root / ".publish.lock").write_text("")

    with pytest.raises(BenchmarkResultArtifactError, match="another result"):
        write_benchmark_result(root, run)


def test_rejects_symlink_added_to_existing_result(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    root = tmp_path / "results"
    first = write_benchmark_result(root, run)
    (first.path / "linked").symlink_to(first.path / "verdict.json")

    with pytest.raises(BenchmarkResultArtifactError, match="symlink"):
        write_benchmark_result(root, run)


def test_cleans_staging_and_lock_after_result_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = completed_run(tmp_path)
    root = tmp_path / "results"
    original_write = artifact_module._write_file
    calls = 0

    def fail_second_write(path: Path, relative_path: str, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated result write failure")
        original_write(path, relative_path, content)

    monkeypatch.setattr(artifact_module, "_write_file", fail_second_write)

    with pytest.raises(OSError, match="simulated"):
        write_benchmark_result(root, run)

    assert list(root.iterdir()) == []


def test_rejects_mutated_raw_evidence(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    run.before_k6_raw = b"tampered"

    with pytest.raises(BenchmarkResultArtifactError, match="raw evidence"):
        write_benchmark_result(tmp_path / "results", run)


def test_rejects_mutated_verdict(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    run.verdict.status = "fail"

    with pytest.raises(BenchmarkResultArtifactError, match="verdict conflicts"):
        write_benchmark_result(tmp_path / "results", run)


def test_rejects_unrestored_execution(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    run.restored = False

    with pytest.raises(BenchmarkResultArtifactError, match="restoration"):
        write_benchmark_result(tmp_path / "results", run)


def test_loader_rejects_rehashed_report_that_conflicts_with_measurements(
    tmp_path: Path,
) -> None:
    _, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)
    (published.path / "report.md").write_text("# Fabricated report\n")

    index_path = published.path / "result.json"
    index = json.loads(index_path.read_text())
    payloads = {
        relative_path: (published.path / relative_path).read_bytes()
        for relative_path in index["files"]
    }
    for relative_path, content in payloads.items():
        index["files"][relative_path] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
    digest = artifact_module._content_digest(payloads)
    index["content_digest_sha256"] = digest
    index["artifact_id"] = f"benchmark-{digest[:32]}"
    index_path.write_bytes(artifact_module._canonical_json(index))
    renamed = published.path.with_name(index["artifact_id"])
    published.path.rename(renamed)

    with pytest.raises(BenchmarkResultArtifactError, match="Markdown report conflicts"):
        load_benchmark_result(renamed)
