from datetime import UTC, datetime, timedelta

import httpx
import pytest

from collector.prometheus import PrometheusClient, PrometheusError, percentile


def test_percentile_interpolates_samples() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2.5


def test_rejects_invalid_observation_window() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        PrometheusClient("http://prometheus").workload_metrics(
            "demo",
            ["api-rs"],
            ["api-abc"],
            "api",
            datetime(2025, 1, 1, tzinfo=UTC),
            0,
        )


def test_collects_workload_percentiles_and_units() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(str(request.url.params["query"]))
        values = [[1, "0.1"], [2, "0.2"], [3, "0.3"]]
        if "memory" in str(request.url.params["query"]):
            values = [[1, str(100 * 1024 * 1024)], [2, str(200 * 1024 * 1024)]]
        return httpx.Response(
            200, json={"status": "success", "data": {"result": [{"values": values}]}}
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["overprovisioned-api-old", "overprovisioned-api-current"],
        ["api-abc", "api-def"],
        "api",
        datetime(2025, 1, 1, tzinfo=UTC),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result.cpu_p95_millicores == pytest.approx(290)
    assert result.memory_p99_mib == pytest.approx(199)
    assert result.cpu_max_millicores == pytest.approx(300)
    assert result.memory_max_mib == pytest.approx(200)
    assert result.step_seconds == 300
    assert result.sample_count == 2
    assert result.metric_pod_count == 1
    assert 0 < result.observation_coverage < 0.01
    assert all('namespace="demo"' in query for query in queries)
    assert all("kube_pod_owner" in query for query in queries)
    assert all("kube_replicaset_owner" not in query for query in queries)
    assert all("overprovisioned-api-old" in query for query in queries)
    assert all("overprovisioned-api-current" in query for query in queries)
    assert all('owner_is_controller="true"}}' not in query for query in queries)
    assert all("sum by (pod)" in query for query in queries)


def test_uses_the_busiest_pod_percentile_instead_of_summing_replicas() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "memory" in str(request.url.params["query"]):
            first = [[1, str(100 * 1024 * 1024)], [2, str(120 * 1024 * 1024)]]
            second = [[1, str(400 * 1024 * 1024)], [2, str(500 * 1024 * 1024)]]
        else:
            first = [[1, "0.1"], [2, "0.2"]]
            second = [[1, "0.4"], [2, "0.6"]]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"pod": "api-abc"}, "values": first},
                        {"metric": {"pod": "api-def"}, "values": second},
                    ]
                },
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["overprovisioned-api-old", "overprovisioned-api-current"],
        ["api-new"],
        "api",
        datetime(2025, 1, 1, tzinfo=UTC),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result.cpu_p95_millicores == pytest.approx(590)
    assert result.memory_p99_mib == pytest.approx(499)
    assert result.cpu_p95_millicores < 790  # The two Pods were not summed together.
    assert result.metric_pod_count == 2  # Includes a previous rollout Pod series.


def test_clips_query_at_current_workload_creation_but_keeps_requested_coverage() -> None:
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(str(request.url.params["start"]))
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"result": [{"values": [[1, "1"], [2, "2"]]}]},
            },
        )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    created_at = now - timedelta(hours=12)
    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["api-current"],
        ["api-current-pod"],
        "api",
        created_at,
        observation_days=7,
        now=now,
    )

    assert starts == [created_at.isoformat(), created_at.isoformat()]
    assert result.query_start == created_at
    assert result.requested_start == now - timedelta(days=7)
    assert result.history_clipped is True
    assert result.observation_coverage < 0.01


def test_rejects_workload_creation_timestamp_in_the_future() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(PrometheusError, match="creation timestamp is in the future"):
        PrometheusClient("http://prometheus").workload_metrics(
            "demo",
            ["api-current"],
            ["api-current-pod"],
            "api",
            now + timedelta(seconds=1),
            now=now,
        )
