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
    cpu_max_millicores: float
    memory_max_mib: float
    observation_days: int
    sample_count: int
    observation_coverage: float


def percentile(values: list[float], quantile: float) -> float:
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    if not values:
        raise PrometheusError("Prometheus returned no samples for the workload")
    if any(not math.isfinite(value) for value in values):
        raise PrometheusError("Prometheus returned a non-finite sample")
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

    def query_range_series(
        self, query: str, start: datetime, end: datetime, step_seconds: int = 300
    ) -> list[list[float]]:
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
        series_values: list[list[float]] = []
        for series in payload.get("data", {}).get("result", []):
            values = [float(value) for _, value in series.get("values", [])]
            if values:
                series_values.append(values)
        return series_values

    def query_range(
        self, query: str, start: datetime, end: datetime, step_seconds: int = 300
    ) -> list[float]:
        return [
            value
            for series in self.query_range_series(query, start, end, step_seconds)
            for value in series
        ]

    def workload_metrics(
        self,
        namespace: str,
        pods: list[str],
        container: str,
        observation_days: int = 7,
        now: datetime | None = None,
    ) -> WorkloadMetrics:
        if observation_days < 1:
            raise ValueError("observation_days must be at least 1")
        end = now or datetime.now(UTC)
        start = end - timedelta(days=observation_days)
        labels = (
            f'namespace="{namespace}",pod=~"{_pod_regex(pods)}",'
            f'container="{container}"'
        )
        cpu_query = f"sum by (pod) (rate(container_cpu_usage_seconds_total{{{labels}}}[5m]))"
        memory_query = f"sum by (pod) (container_memory_working_set_bytes{{{labels}}})"
        cpu_series = self.query_range_series(cpu_query, start, end)
        memory_series = self.query_range_series(memory_query, start, end)
        if not cpu_series or not memory_series:
            raise PrometheusError("Prometheus returned no samples for the workload")

        # Resources are applied per Pod, so retain the busiest replica rather than
        # averaging it away or applying a Deployment-wide sum to every replica.
        cpu_p95_cores = max(percentile(values, 0.95) for values in cpu_series)
        memory_p99_bytes = max(percentile(values, 0.99) for values in memory_series)
        cpu_sample_count = sum(len(values) for values in cpu_series)
        memory_sample_count = sum(len(values) for values in memory_series)
        expected_per_pod = math.floor((end - start).total_seconds() / 300) + 1
        expected_total = expected_per_pod * len(pods)
        observed_samples = min(cpu_sample_count, memory_sample_count)
        return WorkloadMetrics(
            cpu_p95_millicores=cpu_p95_cores * 1000,
            memory_p99_mib=memory_p99_bytes / (1024 * 1024),
            cpu_max_millicores=max(max(values) for values in cpu_series) * 1000,
            memory_max_mib=max(max(values) for values in memory_series) / (1024 * 1024),
            observation_days=observation_days,
            sample_count=observed_samples,
            observation_coverage=min(1.0, observed_samples / expected_total),
        )
