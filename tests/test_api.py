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

