import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, model_validator

from benchmarks.measurement import CollectedMeasurement
from benchmarks.result import (
    BenchmarkMeasurement,
    BenchmarkVerdict,
    compare_benchmarks,
)
from gitops import LoadedProposalBundle, ManifestTarget, load_proposal_bundle


class BenchmarkExecutionError(RuntimeError):
    """Raised when execution or mandatory workload restoration fails."""

    def __init__(
        self,
        stage: str,
        cause: Exception | None,
        restoration_error: Exception | None = None,
    ) -> None:
        self.stage = stage
        self.cause = cause
        self.restoration_error = restoration_error
        details = f"benchmark failed during {stage}"
        if cause is not None:
            details += f": {cause}"
        if restoration_error is not None:
            details += f"; workload restoration also failed: {restoration_error}"
        super().__init__(details)


class ManifestController(Protocol):
    def apply(self, manifest: Path, target: ManifestTarget) -> None: ...

    def wait_for_rollout(self, target: ManifestTarget) -> None: ...


MeasurementCollector = Callable[
    [LoadedProposalBundle, Literal["before", "after"]], CollectedMeasurement
]
CommandRunner = Callable[[Sequence[str]], str]


class BenchmarkRun(BaseModel):
    proposal_id: str
    before: BenchmarkMeasurement
    after: BenchmarkMeasurement
    verdict: BenchmarkVerdict
    before_k6_summary: bytes
    before_k6_raw: bytes
    after_k6_summary: bytes
    after_k6_raw: bytes
    restored: Literal[True] = True

    @model_validator(mode="after")
    def raw_evidence_matches_measurements(self) -> "BenchmarkRun":
        CollectedMeasurement(
            measurement=self.before,
            k6_summary=self.before_k6_summary,
            k6_raw=self.before_k6_raw,
        )
        CollectedMeasurement(
            measurement=self.after,
            k6_summary=self.after_k6_summary,
            k6_raw=self.after_k6_raw,
        )
        return self


class KubectlManifestController:
    def __init__(
        self,
        context: str,
        runner: CommandRunner | None = None,
        rollout_timeout_seconds: int = 120,
    ) -> None:
        if not context:
            raise ValueError("kubectl context must be explicit")
        if rollout_timeout_seconds < 1:
            raise ValueError("rollout timeout must be at least one second")
        self._runner = runner or _run_command
        self._context = context
        self._rollout_timeout_seconds = rollout_timeout_seconds

    def _command(self, *args: str) -> list[str]:
        command = ["kubectl"]
        command.extend(["--context", self._context])
        command.extend(args)
        return command

    def apply(self, manifest: Path, target: ManifestTarget) -> None:
        self._runner(
            self._command(
                "apply",
                "--filename",
                str(manifest),
                "--namespace",
                target.namespace,
            )
        )

    def wait_for_rollout(self, target: ManifestTarget) -> None:
        self._runner(
            self._command(
                "rollout",
                "status",
                f"deployment/{target.deployment}",
                "--namespace",
                target.namespace,
                f"--timeout={self._rollout_timeout_seconds}s",
            )
        )


def execute_benchmark(
    proposal_path: Path,
    controller: ManifestController,
    collect_measurement: MeasurementCollector,
) -> BenchmarkRun:
    proposal = load_proposal_bundle(proposal_path)
    restore_required = False
    primary_error: Exception | None = None
    primary_stage = "proposal_validation"
    result: BenchmarkRun | None = None

    try:
        primary_stage = "apply_before"
        restore_required = True
        controller.apply(proposal.before_manifest, proposal.target)
        primary_stage = "wait_before_rollout"
        controller.wait_for_rollout(proposal.target)
        primary_stage = "measure_before"
        before = collect_measurement(proposal, "before")

        primary_stage = "apply_after"
        controller.apply(proposal.after_manifest, proposal.target)
        primary_stage = "wait_after_rollout"
        controller.wait_for_rollout(proposal.target)
        primary_stage = "measure_after"
        after = collect_measurement(proposal, "after")
        primary_stage = "compare"
        result = BenchmarkRun(
            proposal_id=proposal.artifact_id,
            before=before.measurement,
            after=after.measurement,
            verdict=compare_benchmarks(before.measurement, after.measurement),
            before_k6_summary=before.k6_summary,
            before_k6_raw=before.k6_raw,
            after_k6_summary=after.k6_summary,
            after_k6_raw=after.k6_raw,
        )
    except Exception as exc:
        primary_error = exc

    restoration_error: Exception | None = None
    if restore_required:
        try:
            controller.apply(proposal.before_manifest, proposal.target)
            controller.wait_for_rollout(proposal.target)
        except Exception as exc:
            restoration_error = exc

    if restoration_error is not None:
        raise BenchmarkExecutionError(
            stage=primary_stage if primary_error is not None else "restore_before",
            cause=primary_error,
            restoration_error=restoration_error,
        ) from restoration_error
    if primary_error is not None:
        raise BenchmarkExecutionError(primary_stage, primary_error) from primary_error
    if result is None:
        raise BenchmarkExecutionError("result", RuntimeError("benchmark produced no result"))
    return result


def _run_command(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"kubectl failed: {detail.strip()}") from exc
    return completed.stdout
