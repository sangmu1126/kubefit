import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from benchmarks import (
    AlignedMeasurementCollector,
    BenchmarkMeasurementError,
    K6RunSummary,
    PodRuntimeCounters,
    RuntimeCounterSnapshot,
    SubprocessK6Executor,
    TimedK6Result,
    recovery_from_k6_raw,
)
from gitops import load_proposal_bundle
from tests.test_benchmark import phase
from tests.test_benchmark_runner import published_proposal

START = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)


def summary(proposal_id: str, variant: str = "before") -> K6RunSummary:
    return K6RunSummary.model_validate(
        {
            "schema_version": 1,
            "profile_version": "kubefit-load-v1",
            "proposal_id": proposal_id,
            "variant": variant,
            "dropped_iterations": 0,
            "steady": phase(300, 100, 110),
            "spike": phase(750, 200, 220),
            "recovery": phase(300, 100, 110),
        }
    )


def point(metric: str, timestamp: datetime, value: float) -> str:
    return json.dumps(
        {
            "type": "Point",
            "metric": metric,
            "data": {
                "time": timestamp.isoformat(),
                "value": value,
                "tags": {"kubefit_phase": "recovery"},
            },
        }
    )


def recovery_raw(*windows: float) -> str:
    lines = [point("kubefit_recovery_start", START, 1)]
    for window_index, latency in enumerate(windows):
        for sample in range(25):
            timestamp = START + timedelta(seconds=window_index * 5 + 0.1 + sample * 0.19)
            lines.append(point("http_req_duration", timestamp, latency))
    return "\n".join(lines)


def test_finds_first_complete_recovery_window() -> None:
    result = recovery_from_k6_raw(
        recovery_raw(150, 110, 90),
        summary("proposal-0123456789abcdef0123456789abcdef"),
    )

    assert result == (10.0, True)


def test_reports_incomplete_recovery_without_inventing_success() -> None:
    result = recovery_from_k6_raw(
        recovery_raw(*([111] * 12)),
        summary("proposal-0123456789abcdef0123456789abcdef"),
    )

    assert result == (60.0, False)


def test_skips_underpopulated_recovery_window() -> None:
    lines = recovery_raw(100).splitlines()
    sparse_first_window = "\n".join(lines[:11])
    second_window = recovery_raw(150, 100).splitlines()[26:]

    result = recovery_from_k6_raw(
        "\n".join([sparse_first_window, *second_window]),
        summary("proposal-0123456789abcdef0123456789abcdef"),
    )

    assert result == (10.0, True)


@pytest.mark.parametrize("raw", ["{}", "not-json"])
def test_rejects_missing_or_malformed_recovery_evidence(raw: str) -> None:
    with pytest.raises(BenchmarkMeasurementError, match="raw"):
        recovery_from_k6_raw(
            raw,
            summary("proposal-0123456789abcdef0123456789abcdef"),
        )


def test_k6_executor_uses_isolated_outputs_and_typed_identity(tmp_path: Path) -> None:
    script = tmp_path / "profile.js"
    script.write_text("export default function() {}")
    proposal_id = "proposal-0123456789abcdef0123456789abcdef"
    commands: list[list[str]] = []

    def run(command, timeout):
        commands.append(list(command))
        summary_path = Path(_environment(command, "KUBEFIT_SUMMARY_PATH"))
        raw_path = Path(
            next(value.removeprefix("json=") for value in command if value.startswith("json="))
        )
        summary_path.write_text(summary(proposal_id).model_dump_json())
        raw_path.write_text(recovery_raw(100))
        assert timeout == 200
        return ""

    times = iter([START, START + timedelta(seconds=161)])
    executor = SubprocessK6Executor(
        target_url="http://demo.local/",
        script_path=script,
        runner=run,
        clock=lambda: next(times),
        timeout_seconds=200,
    )

    result = executor.run(proposal_id, "before")

    assert result.traffic_spike_recovered is True
    assert result.traffic_spike_recovery_seconds == 5
    assert (
        result.summary_sha256
        == hashlib.sha256(summary(proposal_id).model_dump_json().encode()).hexdigest()
    )
    assert "KUBEFIT_TARGET_URL=http://demo.local/" in commands[0]
    assert commands[0][:5] == ["k6", "run", "--quiet", "--no-color", "--out"]


def test_k6_executor_rejects_output_identity_mismatch(tmp_path: Path) -> None:
    script = tmp_path / "profile.js"
    script.write_text("export default function() {}")
    proposal_id = "proposal-0123456789abcdef0123456789abcdef"

    def run(command, timeout):
        summary_path = Path(_environment(command, "KUBEFIT_SUMMARY_PATH"))
        raw_path = Path(
            next(
                value.removeprefix("json=")
                for value in command
                if value.startswith("json=")
            )
        )
        summary_path.write_text(summary(proposal_id, "after").model_dump_json())
        raw_path.write_text(recovery_raw(100))
        return ""

    times = iter([START, START + timedelta(seconds=161)])
    executor = SubprocessK6Executor(
        "http://demo.local/", script, runner=run, clock=lambda: next(times)
    )

    with pytest.raises(BenchmarkMeasurementError, match="identity"):
        executor.run(proposal_id, "before")


@pytest.mark.parametrize(
    "target_url",
    [
        "ftp://demo.local/",
        "http://user:secret@demo.local/",
        "http://demo.local/?token=secret",
        "http://demo.local/#fragment",
    ],
)
def test_k6_executor_rejects_target_urls_that_can_leak_credentials(
    tmp_path: Path, target_url: str
) -> None:
    script = tmp_path / "profile.js"
    script.write_text("export default function() {}")

    with pytest.raises(ValueError, match="without credentials"):
        SubprocessK6Executor(target_url, script)


def test_aligned_collector_uses_stable_pod_deltas_window_and_bundle_cost(
    tmp_path: Path,
) -> None:
    proposal = load_proposal_bundle(published_proposal(tmp_path).path)
    snapshots = iter(
        [
            runtime_snapshot(api=(2, False), worker=(1, False)),
            runtime_snapshot(api=(3, True), worker=(1, False)),
        ]
    )
    prometheus = RecordingPrometheus()
    k6 = FakeK6(summary(proposal.artifact_id), recovered=True)
    collector = AlignedMeasurementCollector(
        k6=k6,
        snapshot=lambda _: next(snapshots),
        prometheus=prometheus,
    )

    collected = collector(proposal, "before")
    result = collected.measurement

    assert result.runtime.restart_count == 1
    assert result.runtime.oom_killed_count == 1
    assert result.runtime.cpu_throttling_p95_percent == 2.5
    assert result.provenance.pods == ["api", "worker"]
    assert result.provenance.run_started_at == START
    assert result.request_cost_usd == proposal.before_request_cost_usd
    assert prometheus.call == {
        "namespace": "demo",
        "pods": ["api", "worker"],
        "container": "api",
        "start": START,
        "end": START + timedelta(seconds=161),
    }


def test_aligned_collector_uses_recommended_cost_for_after(tmp_path: Path) -> None:
    proposal = load_proposal_bundle(published_proposal(tmp_path).path)
    snapshots = iter([runtime_snapshot(api=(0, False)), runtime_snapshot(api=(0, False))])
    collector = AlignedMeasurementCollector(
        k6=FakeK6(summary(proposal.artifact_id, "after"), recovered=True),
        snapshot=lambda _: next(snapshots),
        prometheus=RecordingPrometheus(),
    )

    result = collector(proposal, "after").measurement

    assert result.request_cost_usd == proposal.after_request_cost_usd


def test_rejects_pod_replacement_before_prometheus_query(tmp_path: Path) -> None:
    proposal = load_proposal_bundle(published_proposal(tmp_path).path)
    snapshots = iter([runtime_snapshot(old=(0, False)), runtime_snapshot(new=(0, False))])
    prometheus = RecordingPrometheus()
    collector = AlignedMeasurementCollector(
        k6=FakeK6(summary(proposal.artifact_id), recovered=True),
        snapshot=lambda _: next(snapshots),
        prometheus=prometheus,
    )

    with pytest.raises(BenchmarkMeasurementError, match="Pod identity changed"):
        collector(proposal, "before")

    assert prometheus.call is None


def test_rejects_decreasing_restart_counter(tmp_path: Path) -> None:
    proposal = load_proposal_bundle(published_proposal(tmp_path).path)
    snapshots = iter([runtime_snapshot(api=(2, False)), runtime_snapshot(api=(1, False))])
    collector = AlignedMeasurementCollector(
        k6=FakeK6(summary(proposal.artifact_id), recovered=True),
        snapshot=lambda _: next(snapshots),
        prometheus=RecordingPrometheus(),
    )

    with pytest.raises(BenchmarkMeasurementError, match="counter decreased"):
        collector(proposal, "before")


def runtime_snapshot(**pods: tuple[int, bool]) -> RuntimeCounterSnapshot:
    return RuntimeCounterSnapshot(
        pods={
            pod: PodRuntimeCounters(restart_count=values[0], oom_killed=values[1])
            for pod, values in pods.items()
        }
    )


class FakeK6:
    def __init__(self, result_summary: K6RunSummary, recovered: bool) -> None:
        self.result_summary = result_summary
        self.recovered = recovered

    def run(self, proposal_id, variant) -> TimedK6Result:
        assert proposal_id == self.result_summary.proposal_id
        assert variant == self.result_summary.variant
        return TimedK6Result(
            summary=self.result_summary,
            started_at=START,
            finished_at=START + timedelta(seconds=161),
            traffic_spike_recovery_seconds=5 if self.recovered else 60,
            traffic_spike_recovered=self.recovered,
            summary_content=self.result_summary.model_dump_json().encode(),
            raw_content=recovery_raw(100).encode(),
        )


class RecordingPrometheus:
    def __init__(self) -> None:
        self.call = None

    def benchmark_cpu_throttling_p95(self, **kwargs) -> float:
        self.call = kwargs
        return 2.5


def _environment(command: list[str], name: str) -> str:
    prefix = f"{name}="
    return next(value.removeprefix(prefix) for value in command if value.startswith(prefix))
