"""Kubernetes and Prometheus collection adapters."""

from collector.kubernetes import DeploymentResources, KubectlDeploymentCollector
from collector.prometheus import PrometheusClient, WorkloadMetrics

__all__ = [
    "DeploymentResources",
    "KubectlDeploymentCollector",
    "PrometheusClient",
    "WorkloadMetrics",
]
