from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app, create_app
from benchmarks import write_benchmark_result
from evaluator import AnalysisArtifact, AnalysisTarget
from tests.test_analysis_artifact import replayable_analysis
from tests.test_benchmark_artifact import completed_run
from tests.test_benchmark_review import review_request
from tests.test_manifest import eligible_evaluation

client = TestClient(app)


def test_health() -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_source_api_does_not_claim_an_unbuilt_dashboard() -> None:
    response = client.get("/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_app_serves_validated_dashboard_without_shadowing_api(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<!doctype html><title>KubeFit</title><script src="/assets/app.js"></script>'
    )
    (assets / "app.js").write_text("window.kubefit = true;")
    packaged_client = TestClient(create_app(tmp_path))

    dashboard = packaged_client.get("/")
    assert dashboard.status_code == 200
    assert dashboard.headers["content-type"].startswith("text/html")
    assert "<title>KubeFit</title>" in dashboard.text
    assert packaged_client.get("/assets/app.js").text == "window.kubefit = true;"
    assert packaged_client.get("/healthz").json() == {"status": "ok"}
    assert packaged_client.get("/v1/not-found").json() == {"detail": "Not Found"}


@pytest.mark.parametrize("missing", ["index", "assets"])
def test_app_rejects_an_incomplete_dashboard(tmp_path: Path, missing: str) -> None:
    if missing != "index":
        (tmp_path / "index.html").write_text("<!doctype html><title>KubeFit</title>")
    if missing != "assets":
        (tmp_path / "assets").mkdir()

    with pytest.raises(RuntimeError, match=f"dashboard {missing}"):
        create_app(tmp_path)


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
    assert result["current"]["cpu_request_millicores"] == 1000
    assert result["recommendation"]["readiness"]["status"] == "insufficient_data"
    assert result["cost"]["current"]["total_usd"] == "73.000000"
    assert result["cost"]["assumptions"]["price_source"] == "example://local-model"
    assert result["patch_eligibility"]["status"] == "blocked"
    assert result["patch_eligibility"]["checks"][0]["code"] == "recommendation_readiness"


def test_review_analysis_artifact_returns_identity_evaluation_and_checks() -> None:
    artifact = AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    )

    response = client.post(
        "/v1/analysis-reviews",
        content=artifact.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    review = response.json()
    assert review["verification_level"] == "integrity_only"
    assert review["target"] == {
        "namespace": "demo",
        "deployment": "api",
        "container": "api",
    }
    assert review["workload_uid"] == "deployment-uid"
    assert review["evaluation"]["patch_eligibility"]["status"] == "eligible"
    assert len(review["checks"]) == 4


def test_review_analysis_artifact_rejects_tampered_cost() -> None:
    artifact = AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    ).model_dump(mode="json")
    artifact["evaluation"]["cost"]["savings_percent"] = "99.9"

    response = client.post("/v1/analysis-reviews", json=artifact)

    assert response.status_code == 422
    assert "cost comparison conflicts" in response.text


def test_review_analysis_v2_reports_replayed_recommendation() -> None:
    response = client.post(
        "/v1/analysis-reviews",
        content=replayable_analysis().model_dump_json(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    review = response.json()
    assert review["artifact_schema_version"] == 2
    assert review["verification_level"] == "recommendation_replayed"
    assert review["checks"][-1]["code"] == "recommendation_replay"


def test_review_benchmark_returns_index_bound_replayed_verdict(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)

    response = client.post(
        "/v1/benchmark-reviews",
        json=review_request(published.path).model_dump(),
    )

    assert response.status_code == 200
    review = response.json()
    assert review["artifact_id"] == published.artifact_id
    assert review["verification_level"] == "index_bound_replay"
    assert review["verdict"] == run.verdict.model_dump(mode="json")
    assert review["checks"][-1]["code"] == "verdict_replay"


def test_review_benchmark_rejects_tampered_selected_payload(tmp_path: Path) -> None:
    _, run = completed_run(tmp_path)
    published = write_benchmark_result(tmp_path / "results", run)
    request = review_request(published.path).model_dump()
    request["before_json"] += " "

    response = client.post("/v1/benchmark-reviews", json=request)

    assert response.status_code == 422
    assert "before.json" in response.text
