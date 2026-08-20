from pathlib import Path

import pytest

from benchmarks import (
    BenchmarkExecutionError,
    KubectlManifestController,
    execute_benchmark,
)
from gitops import ProposalBundleError, write_proposal_bundle
from tests.test_benchmark import measurement
from tests.test_bundle import proposal_inputs


class RecordingController:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.events: list[str] = []
        self.failures = failures or set()
        self.apply_counts = {"before": 0, "after": 0}
        self.current_variant = "unknown"

    def apply(self, manifest: Path, target) -> None:
        variant = "before" if "before" in manifest.parts else "after"
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
    return write_proposal_bundle(tmp_path / "proposals", patch, evaluation)


def collector(events: list[str], *, fail_variant: str | None = None):
    def collect(proposal, variant):
        events.append(f"measure:{variant}")
        if variant == fail_variant:
            raise RuntimeError(f"measure:{variant}")
        return measurement(variant, proposal_id=proposal.artifact_id)

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
    assert controller.events == [
        "apply:before:1",
        "wait:before:1",
        "measure:before",
        "apply:after:1",
        "wait:after:1",
        "measure:after",
        "apply:before:2",
        "wait:before:2",
    ]


@pytest.mark.parametrize(
    ("failure", "stage"),
    [
        ("apply:before:1", "apply_before"),
        ("wait:before:1", "wait_before_rollout"),
        ("apply:after:1", "apply_after"),
        ("wait:after:1", "wait_after_rollout"),
    ],
)
def test_restores_after_apply_or_rollout_failure(
    tmp_path: Path, failure: str, stage: str
) -> None:
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
        runner=lambda command: commands.append(list(command)) or "",
        rollout_timeout_seconds=90,
    )

    controller.apply(manifest, target)
    controller.wait_for_rollout(target)

    assert commands == [
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
