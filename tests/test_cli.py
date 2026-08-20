import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.cli as cli_module
from api.cli import build_parser
from evaluator import AnalysisArtifact, AnalysisTarget
from gitops import ManifestPatchError
from tests.test_benchmark_artifact import completed_run
from tests.test_manifest import FIXTURES, eligible_evaluation


def eligible_analysis() -> AnalysisArtifact:
    return AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="demo", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    )


def test_analyze_requires_and_parses_explicit_prices() -> None:
    args = build_parser().parse_args(
        [
            "analyze",
            "--deployment",
            "demo",
            "--cpu-core-hour-usd",
            "0.04",
            "--memory-gib-hour-usd",
            "0.005",
            "--price-source",
            "example://local-model",
        ]
    )

    assert args.cpu_core_hour_usd == Decimal("0.04")
    assert args.memory_gib_hour_usd == Decimal("0.005")
    assert args.monthly_hours == Decimal("730")


def test_analyze_rejects_missing_prices() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["analyze", "--deployment", "demo"])


def test_analyze_rejects_non_positive_price() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "analyze",
                "--deployment",
                "demo",
                "--cpu-core-hour-usd",
                "0",
                "--memory-gib-hour-usd",
                "0.005",
                "--price-source",
                "example://local-model",
            ]
        )


def test_benchmark_requires_explicit_mutation_acknowledgement() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "benchmark",
                "--proposal",
                "proposal",
                "--target-url",
                "http://localhost:8080",
                "--context",
                "kind-kubefit",
            ]
        )


def test_benchmark_parses_explicit_local_boundaries() -> None:
    args = build_parser().parse_args(
        [
            "benchmark",
            "--proposal",
            "proposal",
            "--target-url",
            "http://localhost:8080",
            "--context",
            "kind-kubefit",
            "--confirm-disposable-cluster",
            "--results-dir",
            "results",
            "--lock-dir",
            "locks",
        ]
    )

    assert args.proposal == Path("proposal")
    assert args.context == "kind-kubefit"
    assert args.confirm_disposable_cluster is True
    assert args.results_dir == Path("results")
    assert args.lock_dir == Path("locks")


def test_benchmark_command_composes_execution_inside_target_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proposal, run = completed_run(tmp_path)
    events: list[str] = []
    values: dict[str, object] = {}

    class FakeLock:
        def __init__(self, **kwargs) -> None:
            values["lock"] = kwargs

        def __enter__(self):
            events.append("lock-enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            events.append("lock-exit")

    monkeypatch.setattr(cli_module, "KubectlDeploymentCollector", lambda **_: "kubernetes")
    monkeypatch.setattr(cli_module, "DeploymentRuntimeSnapshotter", lambda value: "snapshot")
    monkeypatch.setattr(cli_module, "PrometheusClient", lambda value: "prometheus")
    monkeypatch.setattr(cli_module, "SubprocessK6Executor", lambda **kwargs: "k6")
    monkeypatch.setattr(cli_module, "AlignedMeasurementCollector", lambda **kwargs: "measurement")
    monkeypatch.setattr(cli_module, "KubectlManifestController", lambda **kwargs: "controller")
    monkeypatch.setattr(cli_module, "BenchmarkExecutionLock", FakeLock)

    def execute(path, controller, measurement):
        assert path == proposal.path
        assert controller == "controller"
        assert measurement == "measurement"
        events.append("execute")
        return run

    def publish(path, completed):
        assert path == tmp_path / "results"
        assert completed is run
        events.append("publish")
        return SimpleNamespace(
            artifact_id="benchmark-" + "a" * 32,
            proposal_id=proposal.artifact_id,
            path=path / ("benchmark-" + "a" * 32),
            reused=False,
        )

    monkeypatch.setattr(cli_module, "execute_benchmark", execute)
    monkeypatch.setattr(cli_module, "write_benchmark_result", publish)

    cli_module.main(
        [
            "benchmark",
            "--proposal",
            str(proposal.path),
            "--target-url",
            "http://localhost:8080",
            "--context",
            "kind-kubefit",
            "--confirm-disposable-cluster",
            "--results-dir",
            str(tmp_path / "results"),
            "--lock-dir",
            str(tmp_path / "locks"),
        ]
    )

    assert events == ["lock-enter", "execute", "publish", "lock-exit"]
    assert values["lock"] == {
        "root": tmp_path / "locks",
        "context": "kind-kubefit",
        "namespace": "demo",
        "deployment": "demo",
    }
    output = json.loads(capsys.readouterr().out)
    assert output["artifact_id"] == "benchmark-" + "a" * 32
    assert output["verdict"] == run.verdict.status
    assert output["restored"] is True


def test_benchmark_command_rejects_non_kind_context_before_loading_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_proposal_bundle",
        lambda path: pytest.fail("proposal must not be loaded"),
    )

    with pytest.raises(SystemExit, match="kind-\\*"):
        cli_module.main(
            [
                "benchmark",
                "--proposal",
                "proposal",
                "--target-url",
                "http://localhost:8080",
                "--context",
                "production",
                "--confirm-disposable-cluster",
            ]
        )


def test_propose_creates_and_reuses_immutable_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(eligible_analysis().model_dump_json(indent=2))
    original = (FIXTURES / "input.yaml").read_bytes()
    arguments = [
        "propose",
        "--analysis",
        str(analysis_path),
        "--repository-root",
        str(FIXTURES),
        "--manifest",
        "input.yaml",
        "--output-dir",
        str(tmp_path / "proposals"),
    ]

    cli_module.main(arguments)
    first = json.loads(capsys.readouterr().out)
    cli_module.main(arguments)
    second = json.loads(capsys.readouterr().out)

    assert first["artifact_id"].startswith("proposal-")
    assert first["change_count"] == 4
    assert first["target"] == {
        "namespace": "demo",
        "deployment": "demo",
        "container": "api",
    }
    assert first["reused"] is False
    assert second["artifact_id"] == first["artifact_id"]
    assert second["reused"] is True
    assert (FIXTURES / "input.yaml").read_bytes() == original


def test_propose_rejects_invalid_analysis_before_source_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = tmp_path / "analysis.json"
    analysis.write_text("not-json")
    monkeypatch.setattr(
        cli_module,
        "load_manifest_sources",
        lambda root, paths: pytest.fail("manifest must not be loaded"),
    )

    with pytest.raises(SystemExit, match="analysis JSON is invalid"):
        cli_module.main(
            [
                "propose",
                "--analysis",
                str(analysis),
                "--manifest",
                "demo.yaml",
            ]
        )


def test_propose_rejects_blocked_evaluation_without_output(tmp_path: Path) -> None:
    analysis = eligible_analysis()
    analysis.evaluation.patch_eligibility.status = "blocked"
    analysis.evaluation.patch_eligibility.blocking_reasons = ["test block"]
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(analysis.model_dump_json())
    output = tmp_path / "proposals"

    with pytest.raises(ManifestPatchError, match="test block"):
        cli_module.main(
            [
                "propose",
                "--analysis",
                str(analysis_path),
                "--repository-root",
                str(FIXTURES),
                "--manifest",
                "input.yaml",
                "--output-dir",
                str(output),
            ]
        )

    assert not output.exists()
