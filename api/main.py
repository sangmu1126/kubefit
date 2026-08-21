import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from evaluator import (
    AnalysisArtifact,
    AnalysisReview,
    CostAssumptions,
    EvaluationResult,
    evaluate_resources,
    review_analysis_artifact,
)
from recommender import CurrentResources, ObservedUsage, ResourceRecommendation, recommend_resources

_DASHBOARD_DIRECTORY_ENV = "KUBEFIT_DASHBOARD_DIRECTORY"


class RecommendationRequest(BaseModel):
    current: CurrentResources
    observed: ObservedUsage


class EvaluationRequest(BaseModel):
    current: CurrentResources
    observed: ObservedUsage
    cost_assumptions: CostAssumptions
    replica_count: int = Field(gt=0)


def create_app(dashboard_directory: Path | None = None) -> FastAPI:
    application = FastAPI(
        title="KubeFit API",
        version="0.1.0",
        description="Explainable Kubernetes resource recommendations with GitOps safety.",
    )

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/recommendations", response_model=ResourceRecommendation)
    def create_recommendation(request: RecommendationRequest) -> ResourceRecommendation:
        return recommend_resources(request.current, request.observed)

    @application.post("/v1/evaluations", response_model=EvaluationResult)
    def create_evaluation(request: EvaluationRequest) -> EvaluationResult:
        return evaluate_resources(
            request.current,
            request.observed,
            request.cost_assumptions,
            request.replica_count,
        )

    @application.post("/v1/analysis-reviews", response_model=AnalysisReview)
    def review_analysis(artifact: AnalysisArtifact) -> AnalysisReview:
        return review_analysis_artifact(artifact)

    if dashboard_directory is not None:
        index_file, assets_directory = _validate_dashboard_directory(
            dashboard_directory
        )
        application.mount(
            "/assets",
            StaticFiles(directory=assets_directory),
            name="dashboard-assets",
        )

        @application.get("/", include_in_schema=False)
        def dashboard() -> FileResponse:
            return FileResponse(index_file, media_type="text/html")

    return application


def _validate_dashboard_directory(directory: Path) -> tuple[Path, Path]:
    directory = directory.resolve()
    index_file = directory / "index.html"
    assets_directory = directory / "assets"
    if not index_file.is_file():
        raise RuntimeError(f"dashboard index is missing: {index_file}")
    if not assets_directory.is_dir():
        raise RuntimeError(f"dashboard assets directory is missing: {assets_directory}")
    return index_file, assets_directory


def _dashboard_directory_from_environment() -> Path | None:
    configured = os.environ.get(_DASHBOARD_DIRECTORY_ENV)
    return Path(configured) if configured else None


app = create_app(_dashboard_directory_from_environment())
