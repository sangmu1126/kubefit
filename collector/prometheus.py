import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx


class PrometheusError(RuntimeError):
    """Raised when Prometheus cannot return usable metric data."""


@dataclass(frozen=True)
class WorkloadMetrics:
    cpu_p95_millicores: float
    memory_p99_mib: float
    observation_days: int
    sample_count: int


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise PrometheusError("Prometheus returned no samples for the workload")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * quantile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _pod_regex(pods: list[str]) -> str:
    if not pods:
        raise ValueError("at least one pod is required")
    return "(?:" + "|".join(re.escape(pod) for pod in pods) + ")"


class PrometheusClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=20)

    def query_range(
        self, query: str, start: datetime, end: datetime, step_seconds: int = 300
    ) -> list[float]:
        response = self._client.get(
            "/api/v1/query_range",
            params={
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": step_seconds,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise PrometheusError(payload.get("error", "Prometheus query failed"))
        values: list[float] = []
        for series in payload.get("data", {}).get("result", []):
            values.extend(float(value) for _, value in series.get("values", []))
        return values

    def workload_metrics(
        self,
        namespace: str,
        pods: list[str],
        container: str,
        observation_days: int = 7,
        now: datetime | None = None,
    ) -> WorkloadMetrics:
        end = now or datetime.now(UTC)
        start = end - timedelta(days=observation_days)
        labels = (
            f'namespace="{namespace}",pod=~"{_pod_regex(pods)}",'
            f'container="{container}"'
        )
        cpu_query = f"sum(rate(container_cpu_usage_seconds_total{{{labels}}}[5m]))"
        memory_query = f"sum(container_memory_working_set_bytes{{{labels}}})"
        cpu_cores = self.query_range(cpu_query, start, end)
        memory_bytes = self.query_range(memory_query, start, end)
        return WorkloadMetrics(
            cpu_p95_millicores=percentile(cpu_cores, 0.95) * 1000,
            memory_p99_mib=percentile(memory_bytes, 0.99) / (1024 * 1024),
            observation_days=observation_days,
            sample_count=min(len(cpu_cores), len(memory_bytes)),
        )
