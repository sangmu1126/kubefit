from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_recommendation() -> None:
    response = client.post(
        "/v1/recommendations",
        json={
            "current": {
                "cpu_request_millicores": 1000,
                "cpu_limit_millicores": 2000,
                "memory_request_mib": 2048,
                "memory_limit_mib": 4096,
            },
            "observed": {"cpu_p95_millicores": 230, "memory_p99_mib": 710},
        },
    )

    assert response.status_code == 200
    assert response.json()["recommended"]["cpu_request_millicores"] == 290
    assert response.json()["readiness"]["status"] == "insufficient_data"


def test_create_evaluation_with_explicit_cost_assumptions() -> None:
    response = client.post(
        "/v1/evaluations",
        json={
            "current": {
                "cpu_request_millicores": 1000,
                "cpu_limit_millicores": 2000,
                "memory_request_mib": 2048,
                "memory_limit_mib": 4096,
            },
            "observed": {"cpu_p95_millicores": 230, "memory_p99_mib": 710},
            "cost_assumptions": {
                "cpu_core_hour_usd": "0.04",
                "memory_gib_hour_usd": "0.005",
                "monthly_hours": "730",
                "price_source": "example://local-model",
            },
            "replica_count": 2,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["recommendation"]["readiness"]["status"] == "insufficient_data"
    assert result["cost"]["current"]["total_usd"] == "73.000000"
    assert result["cost"]["assumptions"]["price_source"] == "example://local-model"
    assert result["patch_eligibility"]["status"] == "blocked"
    assert result["patch_eligibility"]["checks"][0]["code"] == "recommendation_readiness"
