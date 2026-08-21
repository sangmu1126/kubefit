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
    observation_days: int | float
    step_seconds: int
    sample_count: int
    observation_coverage: float
    metric_pod_count: int
    cpu_throttling_p95_percent: float | None
    cpu_throttling_max_percent: float | None
    cpu_throttling_sample_count: int
    cpu_throttling_pod_count: int
    cpu_throttling_observation_coverage: float
    requested_start: datetime
    query_start: datetime
    history_clipped: bool


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


def _promql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _promql_regex(values: list[str]) -> str:
    if not values:
        raise ValueError("at least one ReplicaSet is required")
    escaped = [re.escape(value).replace(r"\-", "-") for value in values]
    return _promql_string("(?:" + "|".join(escaped) + ")")


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

    def benchmark_cpu_throttling_p95(
        self,
        namespace: str,
        pods: list[str],
        container: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 5,
        rate_window_seconds: int = 30,
    ) -> float:
        if not pods:
            raise ValueError("at least one benchmark Pod is required")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("benchmark timestamps must include timezone information")
        if end <= start:
            raise ValueError("benchmark end must be later than start")
        if step_seconds < 1 or rate_window_seconds < 1:
            raise ValueError("benchmark query intervals must be positive")
        query_start = start + timedelta(seconds=rate_window_seconds)
        if query_start >= end:
            raise ValueError("benchmark window is too short for the Prometheus rate window")

        namespace_value = _promql_string(namespace)
        container_value = _promql_string(container)
        pod_pattern = _promql_regex(pods)
        labels = (
            f'namespace="{namespace_value}",container="{container_value}",'
            f'pod=~"{pod_pattern}"'
        )
        throttled = (
            "sum by (pod) ("
            "rate(container_cpu_cfs_throttled_periods_total"
            f"{{{labels}}}[{rate_window_seconds}s]))"
        )
        periods = (
            "sum by (pod) ("
            "rate(container_cpu_cfs_periods_total"
            f"{{{labels}}}[{rate_window_seconds}s]))"
        )
        query = (
            f"clamp_max(100 * ({throttled}) / "
            f"clamp_min(({periods}), 1e-9), 100)"
        )
        series = self.query_range_series(query, query_start, end, step_seconds)
        if not series:
            raise PrometheusError("Prometheus returned no throttling samples for benchmark")
        return max(percentile(values, 0.95) for values in series)

    def workload_metrics(
        self,
        namespace: str,
        replica_sets: list[str],
        pods: list[str],
        container: str,
        workload_created_at: datetime,
        observation_days: int | float = 7,
        step_seconds: int = 300,
        now: datetime | None = None,
    ) -> WorkloadMetrics:
        if observation_days <= 0:
            raise ValueError("observation_days must be positive")
        if not pods:
            raise ValueError("at least one current pod is required")
        if step_seconds < 1:
            raise ValueError("step_seconds must be at least 1")
        end = now or datetime.now(UTC)
        if end.tzinfo is None or workload_created_at.tzinfo is None:
            raise ValueError("metric timestamps must include timezone information")
        requested_start = end - timedelta(days=observation_days)
        created_at = workload_created_at.astimezone(UTC)
        if created_at > end:
            raise PrometheusError("workload creation timestamp is in the future")
        query_start = max(requested_start, created_at)
        namespace_value = _promql_string(namespace)
        replica_set_pattern = _promql_regex(replica_sets)
        container_value = _promql_string(container)
        container_labels = (
            f'namespace="{namespace_value}",container="{container_value}"'
        )
        ownership = (
            "max by(namespace,pod) ("
            f'kube_pod_owner{{namespace="{namespace_value}",owner_kind="ReplicaSet",'
            f'owner_name=~"{replica_set_pattern}",'
            'owner_is_controller="true"})'
        )
        cpu_query = (
            "sum by (pod) ("
            f"rate(container_cpu_usage_seconds_total{{{container_labels}}}[5m]) "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        memory_query = (
            "sum by (pod) ("
            f"container_memory_working_set_bytes{{{container_labels}}} "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        throttled_periods = (
            "sum by (pod) ("
            f"rate(container_cpu_cfs_throttled_periods_total{{{container_labels}}}[5m]) "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        total_periods = (
            "sum by (pod) ("
            f"rate(container_cpu_cfs_periods_total{{{container_labels}}}[5m]) "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        throttling_query = (
            f"clamp_max(100 * ({throttled_periods}) / "
            f"clamp_min(({total_periods}), 1e-9), 100)"
        )
        cpu_series = self.query_range_series(cpu_query, query_start, end, step_seconds)
        memory_series = self.query_range_series(memory_query, query_start, end, step_seconds)
        throttling_series = self.query_range_series(
            throttling_query, query_start, end, step_seconds
        )
        if not cpu_series or not memory_series:
            raise PrometheusError("Prometheus returned no samples for the workload")

        # Resources are applied per Pod, so retain the busiest replica rather than
        # averaging it away or applying a Deployment-wide sum to every replica.
        cpu_p95_cores = max(percentile(values, 0.95) for values in cpu_series)
        memory_p99_bytes = max(percentile(values, 0.99) for values in memory_series)
        cpu_sample_count = sum(len(values) for values in cpu_series)
        memory_sample_count = sum(len(values) for values in memory_series)
        expected_per_pod = (
            math.floor((end - requested_start).total_seconds() / step_seconds) + 1
        )
        expected_total = expected_per_pod * len(pods)
        observed_samples = min(cpu_sample_count, memory_sample_count)
        return WorkloadMetrics(
            cpu_p95_millicores=cpu_p95_cores * 1000,
            memory_p99_mib=memory_p99_bytes / (1024 * 1024),
            cpu_max_millicores=max(max(values) for values in cpu_series) * 1000,
            memory_max_mib=max(max(values) for values in memory_series) / (1024 * 1024),
            observation_days=observation_days,
            step_seconds=step_seconds,
            sample_count=observed_samples,
            observation_coverage=min(1.0, observed_samples / expected_total),
            metric_pod_count=min(len(cpu_series), len(memory_series)),
            cpu_throttling_p95_percent=(
                max(percentile(values, 0.95) for values in throttling_series)
                if throttling_series
                else None
            ),
            cpu_throttling_max_percent=(
                max(max(values) for values in throttling_series)
                if throttling_series
                else None
            ),
            cpu_throttling_sample_count=sum(
                len(values) for values in throttling_series
            ),
            cpu_throttling_pod_count=len(throttling_series),
            cpu_throttling_observation_coverage=min(
                1.0,
                sum(len(values) for values in throttling_series) / expected_total,
            ),
            requested_start=requested_start,
            query_start=query_start,
            history_clipped=query_start > requested_start,
        )
