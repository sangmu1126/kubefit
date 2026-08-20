import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from benchmarks import (
    AlignedMeasurementCollector,
    BenchmarkExecutionLock,
    DeploymentRuntimeSnapshotter,
    KubectlManifestController,
    SubprocessK6Executor,
    execute_benchmark,
    write_benchmark_result,
)
from collector import IdentitySnapshotStore, KubectlDeploymentCollector, PrometheusClient
from evaluator import CostAssumptions, evaluate_resources
from gitops import load_proposal_bundle
from recommender import ObservedUsage


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite decimal number")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kubefit")
    subcommands = parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="analyze a Kubernetes Deployment")
    analyze.add_argument("--namespace", "-n", default="default")
    analyze.add_argument("--deployment", required=True)
    analyze.add_argument("--container")
    analyze.add_argument("--prometheus-url", default="http://localhost:9090")
    analyze.add_argument("--days", type=int, default=7)
    analyze.add_argument("--step-seconds", type=int, default=300)
    analyze.add_argument("--identity-store", type=Path)
    analyze.add_argument("--context")
    analyze.add_argument("--cpu-core-hour-usd", required=True, type=_positive_decimal)
    analyze.add_argument("--memory-gib-hour-usd", required=True, type=_positive_decimal)
    analyze.add_argument("--monthly-hours", type=_positive_decimal, default=Decimal("730"))
    analyze.add_argument("--price-source", required=True)
    benchmark = subcommands.add_parser(
        "benchmark", help="benchmark a proposal on a disposable kind cluster"
    )
    benchmark.add_argument("--proposal", required=True, type=Path)
    benchmark.add_argument("--target-url", required=True)
    benchmark.add_argument("--prometheus-url", default="http://localhost:9090")
    benchmark.add_argument("--context", required=True)
    benchmark.add_argument(
        "--confirm-disposable-cluster",
        required=True,
        action="store_true",
        help="acknowledge that this command temporarily applies manifests",
    )
    benchmark.add_argument("--results-dir", type=Path, default=Path("benchmarks/results"))
    benchmark.add_argument("--lock-dir", type=Path, default=Path(".kubefit/benchmark-locks"))
    benchmark.add_argument(
        "--k6-script",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks" / "k6" / "resource_profile.js",
    )
    benchmark.add_argument("--rollout-timeout-seconds", type=int, default=120)
    benchmark.add_argument("--k6-timeout-seconds", type=int, default=240)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "benchmark":
        _run_benchmark(args)
        return
    _run_analyze(args)


def _run_analyze(args: argparse.Namespace) -> None:
    workload = KubectlDeploymentCollector(context=args.context).collect(
        args.namespace, args.deployment, args.container
    )
    replica_sets = workload.replica_sets
    if args.identity_store is not None:
        identity = IdentitySnapshotStore(args.identity_store).remember(
            namespace=workload.namespace,
            name=workload.name,
            uid=workload.uid,
            created_at=workload.created_at,
            replica_sets=workload.replica_sets,
        )
        replica_sets = list(identity.replica_sets)
    metrics = PrometheusClient(args.prometheus_url).workload_metrics(
        workload.namespace,
        replica_sets,
        workload.pods,
        workload.container,
        workload.created_at,
        observation_days=args.days,
        step_seconds=args.step_seconds,
    )
    observed = ObservedUsage(
        cpu_p95_millicores=metrics.cpu_p95_millicores,
        memory_p99_mib=metrics.memory_p99_mib,
        cpu_max_millicores=metrics.cpu_max_millicores,
        memory_max_mib=metrics.memory_max_mib,
        observation_days=metrics.observation_days,
        step_seconds=metrics.step_seconds,
        sample_count=metrics.sample_count,
        observation_coverage=metrics.observation_coverage,
        desired_replicas=workload.desired_replicas,
        available_replicas=workload.available_replicas,
        observed_replicas=len(workload.pods),
        metric_pod_count=metrics.metric_pod_count,
        workload_uid=workload.uid,
        workload_created_at=workload.created_at,
        history_clipped=metrics.history_clipped,
        authorized_replica_set_count=len(replica_sets),
        identity_snapshot_enabled=args.identity_store is not None,
        cpu_throttling_p95_percent=metrics.cpu_throttling_p95_percent,
        cpu_throttling_max_percent=metrics.cpu_throttling_max_percent,
        cpu_throttling_sample_count=metrics.cpu_throttling_sample_count,
        cpu_throttling_pod_count=metrics.cpu_throttling_pod_count,
        cpu_throttling_observation_coverage=(metrics.cpu_throttling_observation_coverage),
        container_status_count=workload.container_status_count,
        restart_count=workload.restart_count,
        oom_killed_count=workload.oom_killed_count,
    )
    result = evaluate_resources(
        workload.resources,
        observed,
        CostAssumptions(
            cpu_core_hour_usd=args.cpu_core_hour_usd,
            memory_gib_hour_usd=args.memory_gib_hour_usd,
            monthly_hours=args.monthly_hours,
            price_source=args.price_source,
        ),
        workload.desired_replicas,
    )
    print(result.model_dump_json(indent=2))


def _run_benchmark(args: argparse.Namespace) -> None:
    if not args.context.startswith("kind-"):
        raise SystemExit("benchmark command only accepts an explicit kind-* context")
    proposal = load_proposal_bundle(args.proposal)
    kubernetes = KubectlDeploymentCollector(context=args.context)
    measurement = AlignedMeasurementCollector(
        k6=SubprocessK6Executor(
            target_url=args.target_url,
            script_path=args.k6_script,
            timeout_seconds=args.k6_timeout_seconds,
        ),
        snapshot=DeploymentRuntimeSnapshotter(kubernetes),
        prometheus=PrometheusClient(args.prometheus_url),
    )
    controller = KubectlManifestController(
        context=args.context,
        rollout_timeout_seconds=args.rollout_timeout_seconds,
    )
    with BenchmarkExecutionLock(
        root=args.lock_dir,
        context=args.context,
        namespace=proposal.target.namespace,
        deployment=proposal.target.deployment,
    ):
        run = execute_benchmark(args.proposal, controller, measurement)
        artifact = write_benchmark_result(args.results_dir, run)
    print(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "path": str(artifact.path),
                "proposal_id": artifact.proposal_id,
                "verdict": run.verdict.status,
                "restored": run.restored,
                "reused": artifact.reused,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
