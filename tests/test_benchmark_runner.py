import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarks import (
    BenchmarkExecutionError,
    CollectedMeasurement,
    K6RunSummary,
    KubectlManifestController,
    execute_benchmark,
)
from evaluator import AnalysisArtifact, AnalysisTarget
from gitops import ProposalBundleError, write_proposal_bundle
from tests.test_benchmark import measurement
from tests.test_bundle import proposal_inputs


class RecordingController:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.events: list[str] = []
        self.failures = failures or set()
        self.apply_counts = {"before": 0, "after": 0}
        self.applied_manifests: list[Path] = []
        self.current_variant = "unknown"

    def verify_identity(self, target, workload_uid, workload_created_at) -> None:
        self.events.append("verify")
        if "verify" in self.failures:
            self.failures.remove("verify")
            raise RuntimeError("verify")

    def apply(self, manifest: Path, target) -> None:
        self.applied_manifests.append(manifest)
        variant = manifest.stem
        self.current_variant = variant
        self.apply_counts[variant] += 1
        event = f"apply:{variant}:{self.apply_counts[variant]}"
        self.events.append(event)
        if event in self.failures:
            self.failures.remove(event)
            raise RuntimeError(event)

    def wait_for_rollout(self, target) -> None:
        event = f"wait:{self.current_variant}:{self.apply_counts[self.current_variant]}"
        self.events.append(event)
        if event in self.failures:
            self.failures.remove(event)
            raise RuntimeError(event)


def published_proposal(tmp_path: Path):
    _, evaluation, patch = proposal_inputs()
    analysis = AnalysisArtifact(
        target=AnalysisTarget(**patch.report.target.model_dump()),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=evaluation,
    )
    return write_proposal_bundle(tmp_path / "proposals", patch, evaluation, analysis=analysis)


def collector(events: list[str], *, fail_variant: str | None = None):
    def collect(proposal, variant):
        events.append(f"measure:{variant}")
        if variant == fail_variant:
            raise RuntimeError(f"measure:{variant}")
        measured = measurement(variant, proposal_id=proposal.artifact_id)
        summary_bytes = (
            K6RunSummary.model_validate(measured.model_dump()).model_dump_json().encode()
        )
        raw_bytes = f"raw:{variant}".encode()
        measured = measured.model_copy(
            update={
                "provenance": measured.provenance.model_copy(
                    update={
                        "k6_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
                        "k6_raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    }
                )
            }
        )
        return CollectedMeasurement(
            measurement=measured,
            k6_summary=summary_bytes,
            k6_raw=raw_bytes,
        )

    return collect


def test_executes_fixed_order_and_restores_before_returning(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    controller = RecordingController()

    result = execute_benchmark(
        proposal.path,
        controller,
        collector(controller.events),
    )

    assert result.proposal_id == proposal.artifact_id
    assert result.verdict.status == "pass"
    assert result.restored is True
    assert result.before_k6_raw == b"raw:before"
    assert controller.events == [
        "verify",
        "apply:before:1",
        "wait:before:1",
        "measure:before",
        "apply:after:1",
        "wait:after:1",
        "measure:after",
        "apply:before:2",
        "wait:before:2",
    ]
    assert all(
        manifest.parent.name == "manifests"
        and manifest.parent.parent.name == "benchmark"
        for manifest in controller.applied_manifests
    )
    assert all(
        "kind: Service" not in manifest.read_text()
        for manifest in controller.applied_manifests
    )


@pytest.mark.parametrize(
    ("failure", "stage"),
    [
        ("apply:before:1", "apply_before"),
        ("wait:before:1", "wait_before_rollout"),
        ("apply:after:1", "apply_after"),
        ("wait:after:1", "wait_after_rollout"),
    ],
)
def test_restores_after_apply_or_rollout_failure(tmp_path: Path, failure: str, stage: str) -> None:
    proposal = published_proposal(tmp_path)
    controller = RecordingController({failure})

    with pytest.raises(BenchmarkExecutionError) as raised:
        execute_benchmark(proposal.path, controller, collector(controller.events))

    assert raised.value.stage == stage
    assert raised.value.restoration_error is None
    assert controller.events[-2:] == ["apply:before:2", "wait:before:2"]


def test_restores_after_measurement_failure(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    controller = RecordingController()

    with pytest.raises(BenchmarkExecutionError) as raised:
        execute_benchmark(
            proposal.path,
            controller,
            collector(controller.events, fail_variant="after"),
        )

    assert raised.value.stage == "measure_after"
    assert controller.events[-2:] == ["apply:before:2", "wait:before:2"]


def test_identity_failure_never_applies_or_restores(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    controller = RecordingController({"verify"})

    with pytest.raises(BenchmarkExecutionError) as raised:
        execute_benchmark(proposal.path, controller, collector(controller.events))

    assert raised.value.stage == "verify_workload_identity"
    assert controller.events == ["verify"]


def test_reports_restoration_failure_after_successful_measurements(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    controller = RecordingController({"wait:before:2"})

    with pytest.raises(BenchmarkExecutionError) as raised:
        execute_benchmark(proposal.path, controller, collector(controller.events))

    assert raised.value.stage == "restore_before"
    assert raised.value.cause is None
    assert isinstance(raised.value.restoration_error, RuntimeError)


def test_preserves_primary_and_restoration_errors(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    controller = RecordingController({"apply:before:2"})

    with pytest.raises(BenchmarkExecutionError) as raised:
        execute_benchmark(
            proposal.path,
            controller,
            collector(controller.events, fail_variant="after"),
        )

    assert raised.value.stage == "measure_after"
    assert str(raised.value.cause) == "measure:after"
    assert str(raised.value.restoration_error) == "apply:before:2"


def test_rejects_tampered_bundle_before_cluster_commands(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    proposal.path.joinpath("patch.diff").write_text("tampered\n")
    controller = RecordingController()

    with pytest.raises(ProposalBundleError, match="size changed"):
        execute_benchmark(proposal.path, controller, collector(controller.events))

    assert controller.events == []


def test_kubectl_controller_uses_explicit_context_target_and_timeout(tmp_path: Path) -> None:
    commands: list[list[str]] = []
    manifest = tmp_path / "candidate.yaml"
    target = proposal_inputs()[2].report.target
    controller = KubectlManifestController(
        context="kind-kubefit",
        runner=lambda command: (
            commands.append(list(command))
            or json.dumps(
                {
                    "metadata": {
                        "uid": "deployment-uid",
                        "creationTimestamp": "2026-08-21T00:00:00Z",
                    }
                }
            )
        ),
        rollout_timeout_seconds=90,
    )

    controller.verify_identity(
        target,
        "deployment-uid",
        datetime(2026, 8, 21, tzinfo=UTC),
    )
    controller.apply(manifest, target)
    controller.wait_for_rollout(target)

    assert commands == [
        [
            "kubectl",
            "--context",
            "kind-kubefit",
            "get",
            "deployment",
            "demo",
            "--namespace",
            "demo",
            "--output",
            "json",
        ],
        [
            "kubectl",
            "--context",
            "kind-kubefit",
            "apply",
            "--filename",
            str(manifest),
            "--namespace",
            "demo",
        ],
        [
            "kubectl",
            "--context",
            "kind-kubefit",
            "rollout",
            "status",
            "deployment/demo",
            "--namespace",
            "demo",
            "--timeout=90s",
        ],
    ]


def test_kubectl_controller_requires_explicit_context() -> None:
    with pytest.raises(ValueError, match="context must be explicit"):
        KubectlManifestController(context="")


def test_kubectl_controller_rejects_recreated_deployment_identity() -> None:
    controller = KubectlManifestController(
        context="kind-kubefit",
        runner=lambda command: json.dumps(
            {
                "metadata": {
                    "uid": "different-uid",
                    "creationTimestamp": "2026-08-21T00:00:00Z",
                }
            }
        ),
    )

    with pytest.raises(RuntimeError, match="identity changed"):
        controller.verify_identity(
            proposal_inputs()[2].report.target,
            "deployment-uid",
            datetime(2026, 8, 21, tzinfo=UTC),
        )
