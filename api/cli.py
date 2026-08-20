import argparse
import json
import os
import re
from datetime import timedelta
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
from collector import (
    DeploymentResources,
    IdentitySnapshotStore,
    KubectlDeploymentCollector,
    PrometheusClient,
    WorkloadMetrics,
)
from evaluator import (
    AnalysisArtifact,
    AnalysisTarget,
    CostAssumptions,
    assess_observation_readiness,
    evaluate_resources,
)
from gitops import (
    GitHubRestClient,
    ManifestTarget,
    build_pull_request_plan,
    commit_pull_request_plan,
    generate_resource_patch,
    load_manifest_sources,
    load_proposal_bundle,
    publish_pull_request,
    write_proposal_bundle,
)
from recommender import ObservedUsage


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite decimal number")
    return parsed


def _environment_variable_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise argparse.ArgumentTypeError("must be a valid environment variable name")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kubefit")
    subcommands = parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="analyze a Kubernetes Deployment")
    _add_observation_arguments(analyze)
    analyze.add_argument("--cpu-core-hour-usd", required=True, type=_positive_decimal)
    analyze.add_argument("--memory-gib-hour-usd", required=True, type=_positive_decimal)
    analyze.add_argument("--monthly-hours", type=_positive_decimal, default=Decimal("730"))
    analyze.add_argument("--price-source", required=True)
    readiness = subcommands.add_parser(
        "readiness", help="explain whether observation evidence is proposal-ready"
    )
    _add_observation_arguments(readiness)
    propose = subcommands.add_parser(
        "propose", help="create an immutable proposal from an analysis and YAML"
    )
    propose.add_argument("--analysis", required=True, type=Path)
    propose.add_argument("--repository-root", type=Path, default=Path("."))
    propose.add_argument("--manifest", required=True, nargs="+", type=Path)
    propose.add_argument("--output-dir", type=Path, default=Path(".kubefit/proposals"))
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
    publish = subcommands.add_parser(
        "publish", help="commit a verified proposal and open or reuse a GitHub draft PR"
    )
    publish.add_argument("--proposal", required=True, type=Path)
    publish.add_argument("--benchmark", required=True, type=Path)
    publish.add_argument("--repository-root", type=Path, default=Path("."))
    publish.add_argument("--remote", default="origin")
    publish.add_argument(
        "--github-token-env",
        type=_environment_variable_name,
        default="GITHUB_TOKEN",
        metavar="NAME",
        help="environment variable containing the GitHub token (default: GITHUB_TOKEN)",
    )
    publish.add_argument(
        "--confirm-publish",
        required=True,
        action="store_true",
        help="acknowledge that this command creates a branch and draft pull request",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "publish":
        _run_publish(args)
        return
    if args.command == "benchmark":
        _run_benchmark(args)
        return
    if args.command == "propose":
        _run_propose(args)
        return
    if args.command == "readiness":
        _run_readiness(args)
        return
    _run_analyze(args)


def _add_observation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--namespace", "-n", default="default")
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--container")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--step-seconds", type=int, default=300)
    parser.add_argument("--identity-store", type=Path)
    parser.add_argument("--context")


def _collect_observation(
    args: argparse.Namespace,
) -> tuple[DeploymentResources, WorkloadMetrics, ObservedUsage]:
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
    return workload, metrics, observed


def _run_analyze(args: argparse.Namespace) -> None:
    workload, _, observed = _collect_observation(args)
    evaluation = evaluate_resources(
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
    result = AnalysisArtifact(
        target=AnalysisTarget(
            namespace=workload.namespace,
            deployment=workload.name,
            container=workload.container,
        ),
        workload_uid=workload.uid,
        workload_created_at=workload.created_at,
        evaluation=evaluation,
    )
    print(result.model_dump_json(indent=2))


def _run_readiness(args: argparse.Namespace) -> None:
    workload, metrics, observed = _collect_observation(args)
    report = assess_observation_readiness(
        target=AnalysisTarget(
            namespace=workload.namespace,
            deployment=workload.name,
            container=workload.container,
        ),
        current=workload.resources,
        observed=observed,
        observed_at=metrics.requested_start + timedelta(days=metrics.observation_days),
    )
    print(report.model_dump_json(indent=2))


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


def _run_propose(args: argparse.Namespace) -> None:
    if args.analysis.is_symlink() or not args.analysis.is_file():
        raise SystemExit("analysis must be a regular, non-symlinked JSON file")
    try:
        analysis = AnalysisArtifact.model_validate_json(args.analysis.read_bytes())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"analysis JSON is invalid: {exc}") from exc
    sources = load_manifest_sources(args.repository_root, args.manifest)
    target = ManifestTarget(
        namespace=analysis.target.namespace,
        deployment=analysis.target.deployment,
        container=analysis.target.container,
    )
    patch = generate_resource_patch(sources, target, analysis.evaluation)
    proposal = write_proposal_bundle(
        args.output_dir,
        patch,
        analysis.evaluation,
        analysis=analysis,
    )
    print(
        json.dumps(
            {
                "artifact_id": proposal.artifact_id,
                "path": str(proposal.path),
                "reused": proposal.reused,
                "target": target.model_dump(),
                "change_count": len(patch.report.changes),
                "warnings": patch.report.eligibility_warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_publish(args: argparse.Namespace) -> None:
    token = os.environ.get(args.github_token_env)
    if token is None or not token.strip():
        raise SystemExit(
            f"publish requires a non-empty {args.github_token_env} environment variable"
        )
    try:
        github = GitHubRestClient(token)
        plan = build_pull_request_plan(args.proposal, args.benchmark)
        commit = commit_pull_request_plan(args.repository_root, plan)
        published = publish_pull_request(
            args.repository_root,
            plan,
            commit,
            github,
            remote=args.remote,
        )
    except Exception as exc:
        detail = str(exc).replace(token, "[REDACTED]")
        raise SystemExit(f"publish failed: {detail}") from None
    print(
        json.dumps(
            {
                "repository": (
                    f"{published.repository.owner}/{published.repository.name}"
                ),
                "remote": published.remote,
                "branch": published.branch_name,
                "commit_sha": published.commit_sha,
                "branch_reused": published.branch_reused,
                "pull_request_number": published.pull_request_number,
                "pull_request_url": published.pull_request_url,
                "pull_request_reused": published.pull_request_reused,
                "draft": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
