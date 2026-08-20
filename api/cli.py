import argparse
from pathlib import Path

from collector import IdentitySnapshotStore, KubectlDeploymentCollector, PrometheusClient
from recommender import ObservedUsage, recommend_resources


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
    return parser


def main() -> None:
    args = build_parser().parse_args()
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
    recommendation = recommend_resources(
        workload.resources,
        ObservedUsage(
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
        ),
    )
    print(recommendation.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
