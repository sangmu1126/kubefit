import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

import gitops.bundle as bundle_module
from evaluator import AnalysisArtifact, AnalysisTarget
from gitops import (
    ManifestSource,
    ProposalBundleError,
    generate_resource_patch,
    load_proposal_bundle,
    write_proposal_bundle,
)
from tests.test_manifest import FIXTURES, eligible_evaluation, target


def proposal_inputs():
    source = FIXTURES / "input.yaml"
    evaluation = eligible_evaluation()
    patch = generate_resource_patch(
        [ManifestSource(path="deploy/demo.yaml", content=source.read_text())],
        target(),
        evaluation,
    )
    return source, evaluation, patch


def test_publishes_complete_hashed_proposal_bundle(tmp_path: Path) -> None:
    source, evaluation, patch = proposal_inputs()

    result = write_proposal_bundle(tmp_path / "proposals", patch, evaluation)

    assert result.reused is False
    assert result.artifact_id.startswith("proposal-")
    assert result.path.is_dir()
    assert source.read_text() == patch.original_content
    assert (result.path / "manifests/before/deploy/demo.yaml").read_text() == (
        patch.original_content
    )
    assert (result.path / "manifests/after/deploy/demo.yaml").read_text() == (patch.patched_content)
    before_executable = result.path / "benchmark/manifests/before.yaml"
    after_executable = result.path / "benchmark/manifests/after.yaml"
    assert "kind: Service" not in before_executable.read_text()
    assert "kind: Service" not in after_executable.read_text()
    assert "kind: Deployment" in before_executable.read_text()
    assert "kind: Deployment" in after_executable.read_text()
    index = json.loads((result.path / "artifact.json").read_text())
    assert index["artifact_id"] == result.artifact_id
    assert set(index["files"]) == set(result.files) - {"artifact.json"}
    for relative_path, metadata in index["files"].items():
        content = (result.path / relative_path).read_bytes()
        assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
        assert metadata["size_bytes"] == len(content)
        assert stat.S_IMODE((result.path / relative_path).stat().st_mode) == 0o600
    context = json.loads((result.path / "benchmark-context.json").read_text())
    assert context["before_resources"] == evaluation.current.model_dump()
    assert context["after_resources"] == evaluation.recommendation.recommended.model_dump()
    assert "latency_p99_ms" in context["required_metrics"]
    assert "k6_raw_sha256" in context["required_metrics"]
    assert "traffic_spike_recovered" in context["required_metrics"]


def test_loads_and_revalidates_published_bundle(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    published = write_proposal_bundle(tmp_path / "proposals", patch, evaluation)

    loaded = load_proposal_bundle(published.path)

    assert loaded.artifact_id == published.artifact_id
    assert loaded.source_path == "deploy/demo.yaml"
    assert loaded.target == patch.report.target
    assert loaded.before_source_manifest.read_text() == patch.original_content
    assert loaded.after_source_manifest.read_text() == patch.patched_content
    assert loaded.before_manifest == published.path / "benchmark/manifests/before.yaml"
    assert loaded.after_manifest == published.path / "benchmark/manifests/after.yaml"
    assert "kind: Service" not in loaded.before_manifest.read_text()
    assert "kind: Service" not in loaded.after_manifest.read_text()
    assert loaded.before_request_cost_usd == evaluation.cost.current.total_usd
    assert loaded.after_request_cost_usd == evaluation.cost.recommended.total_usd


def test_persists_and_loads_analysis_workload_identity(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    analysis = AnalysisArtifact(
        target=AnalysisTarget(**patch.report.target.model_dump()),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=evaluation,
    )

    published = write_proposal_bundle(tmp_path / "proposals", patch, evaluation, analysis=analysis)
    loaded = load_proposal_bundle(published.path)

    assert "analysis.json" in published.files
    assert loaded.workload_uid == "deployment-uid"
    assert loaded.workload_created_at == datetime(2026, 8, 21, tzinfo=UTC)


def test_rejects_analysis_target_mismatch_before_creating_output(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    analysis = AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="other", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=evaluation,
    )
    output = tmp_path / "proposals"

    with pytest.raises(ProposalBundleError, match="analysis target conflicts"):
        write_proposal_bundle(output, patch, evaluation, analysis=analysis)

    assert not output.exists()


def test_reuses_byte_identical_bundle_idempotently(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    root = tmp_path / "proposals"

    first = write_proposal_bundle(root, patch, evaluation)
    second = write_proposal_bundle(root, patch, evaluation)

    assert second.artifact_id == first.artifact_id
    assert second.path == first.path
    assert second.reused is True


def test_artifact_id_is_stable_across_output_roots(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()

    first = write_proposal_bundle(tmp_path / "first", patch, evaluation)
    second = write_proposal_bundle(tmp_path / "second", patch, evaluation)

    assert second.artifact_id == first.artifact_id


def test_rejects_modified_existing_bundle_without_overwriting(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    root = tmp_path / "proposals"
    first = write_proposal_bundle(root, patch, evaluation)
    diff_path = first.path / "patch.diff"
    diff_path.write_text("tampered\n")

    with pytest.raises(ProposalBundleError, match="was modified"):
        write_proposal_bundle(root, patch, evaluation)

    assert diff_path.read_text() == "tampered\n"


def test_rejects_extra_file_in_existing_bundle(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    root = tmp_path / "proposals"
    first = write_proposal_bundle(root, patch, evaluation)
    (first.path / "unexpected.txt").write_text("unexpected")

    with pytest.raises(ProposalBundleError, match="file set"):
        write_proposal_bundle(root, patch, evaluation)


def test_rejects_active_publication_lock(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    root = tmp_path / "proposals"
    root.mkdir()
    (root / ".publish.lock").write_text("")

    with pytest.raises(ProposalBundleError, match="another proposal publication"):
        write_proposal_bundle(root, patch, evaluation)


def test_cleans_staging_and_lock_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, evaluation, patch = proposal_inputs()
    root = tmp_path / "proposals"
    original_write = bundle_module._write_file
    calls = 0

    def fail_second_write(path: Path, relative_path: str, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated write failure")
        original_write(path, relative_path, content)

    monkeypatch.setattr(bundle_module, "_write_file", fail_second_write)

    with pytest.raises(OSError, match="simulated"):
        write_proposal_bundle(root, patch, evaluation)

    assert list(root.iterdir()) == []


def test_revalidates_mutated_report_path(tmp_path: Path) -> None:
    _, evaluation, patch = proposal_inputs()
    patch.report.source_path = "../outside.yaml"

    with pytest.raises(ProposalBundleError, match="repository-relative"):
        write_proposal_bundle(tmp_path / "proposals", patch, evaluation)


def test_rejects_rehashed_benchmark_manifest_that_conflicts_with_source(
    tmp_path: Path,
) -> None:
    _, evaluation, patch = proposal_inputs()
    published = write_proposal_bundle(tmp_path / "proposals", patch, evaluation)
    executable = published.path / "benchmark/manifests/before.yaml"
    executable.write_text(executable.read_text().replace('cpu: "1000m"', 'cpu: "999m"'))

    artifact_path = published.path / "artifact.json"
    index = json.loads(artifact_path.read_text())
    payloads = {
        relative_path: (published.path / relative_path).read_bytes()
        for relative_path in index["files"]
    }
    for relative_path, content in payloads.items():
        index["files"][relative_path] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
    digest = bundle_module._content_digest(payloads)
    index["content_digest_sha256"] = digest
    index["artifact_id"] = f"proposal-{digest[:32]}"
    artifact_path.write_bytes(bundle_module._canonical_json(index))
    renamed = published.path.with_name(index["artifact_id"])
    published.path.rename(renamed)

    with pytest.raises(ProposalBundleError, match="conflicts with its source"):
        load_proposal_bundle(renamed)
