import argparse

from collector import KubectlDeploymentCollector, PrometheusClient
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
    analyze.add_argument("--context")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    workload = KubectlDeploymentCollector(context=args.context).collect(
        args.namespace, args.deployment, args.container
    )
    metrics = PrometheusClient(args.prometheus_url).workload_metrics(
        workload.namespace,
        workload.name,
        workload.pods,
        workload.container,
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
        ),
    )
    print(recommendation.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
