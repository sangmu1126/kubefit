from datetime import UTC, datetime

import httpx
import pytest

from collector.prometheus import PrometheusClient, percentile


def test_percentile_interpolates_samples() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2.5


def test_rejects_invalid_observation_window() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        PrometheusClient("http://prometheus").workload_metrics("demo", ["api"], "api", 0)


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
        "demo", ["api-abc", "api-def"], "api", now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert result.cpu_p95_millicores == pytest.approx(290)
    assert result.memory_p99_mib == pytest.approx(199)
    assert result.cpu_max_millicores == pytest.approx(300)
    assert result.memory_max_mib == pytest.approx(200)
    assert result.sample_count == 2
    assert 0 < result.observation_coverage < 0.01
    assert all('namespace="demo"' in query for query in queries)
    assert all("api\\-abc" in query for query in queries)
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
        "demo", ["api-abc", "api-def"], "api", now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert result.cpu_p95_millicores == pytest.approx(590)
    assert result.memory_p99_mib == pytest.approx(499)
    assert result.cpu_p95_millicores < 790  # The two Pods were not summed together.
