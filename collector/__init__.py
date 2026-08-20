"""Kubernetes and Prometheus collection adapters."""

from collector.identity import IdentitySnapshotStore, WorkloadIdentity
from collector.kubernetes import DeploymentResources, KubectlDeploymentCollector
from collector.prometheus import PrometheusClient, WorkloadMetrics

__all__ = [
    "DeploymentResources",
    "IdentitySnapshotStore",
    "KubectlDeploymentCollector",
    "PrometheusClient",
    "WorkloadIdentity",
    "WorkloadMetrics",
]
