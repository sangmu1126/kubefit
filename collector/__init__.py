"""Kubernetes and Prometheus collection adapters."""

from collector.identity import IdentitySnapshotStore, WorkloadIdentity
from collector.kubernetes import (
    DeploymentResources,
    KubectlDeploymentCollector,
    PodContainerRuntimeStatus,
    parse_cpu_millicores,
    parse_memory_mib,
)
from collector.prometheus import PrometheusClient, WorkloadMetrics

__all__ = [
    "DeploymentResources",
    "IdentitySnapshotStore",
    "KubectlDeploymentCollector",
    "PrometheusClient",
    "PodContainerRuntimeStatus",
    "WorkloadIdentity",
    "WorkloadMetrics",
    "parse_cpu_millicores",
    "parse_memory_mib",
]
