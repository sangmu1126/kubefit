import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from benchmarks import (
    BenchmarkReview,
    BenchmarkReviewRequest,
    review_benchmark_result,
    review_full_benchmark_result,
)
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
_BENCHMARK_RESULTS_DIRECTORY_ENV = "KUBEFIT_BENCHMARK_RESULTS_DIRECTORY"


class RecommendationRequest(BaseModel):
    current: CurrentResources
    observed: ObservedUsage


class EvaluationRequest(BaseModel):
    current: CurrentResources
    observed: ObservedUsage
    cost_assumptions: CostAssumptions
    replica_count: int = Field(gt=0)


def create_app(
    dashboard_directory: Path | None = None,
    benchmark_results_directory: Path | None = None,
) -> FastAPI:
    if benchmark_results_directory is not None:
        benchmark_results_directory = _validate_benchmark_results_directory(
            benchmark_results_directory
        )
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

    @application.post("/v1/benchmark-reviews", response_model=BenchmarkReview)
    def review_benchmark(request: BenchmarkReviewRequest) -> BenchmarkReview:
        try:
            return review_benchmark_result(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get(
        "/v1/benchmark-results/{artifact_id}/review",
        response_model=BenchmarkReview,
    )
    def review_stored_benchmark(
        artifact_id: Annotated[
            str, ApiPath(pattern=r"^benchmark-[0-9a-f]{32}$")
        ],
    ) -> BenchmarkReview:
        if benchmark_results_directory is None:
            raise HTTPException(
                status_code=404,
                detail="stored benchmark review is not configured",
            )
        result_path = benchmark_results_directory / artifact_id
        if not result_path.exists():
            raise HTTPException(status_code=404, detail="benchmark result was not found")
        try:
            return review_full_benchmark_result(result_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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


def _validate_benchmark_results_directory(directory: Path) -> Path:
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("benchmark results directory must be a regular directory")
    return directory.resolve()


def _dashboard_directory_from_environment() -> Path | None:
    configured = os.environ.get(_DASHBOARD_DIRECTORY_ENV)
    return Path(configured) if configured else None


def _benchmark_results_directory_from_environment() -> Path | None:
    configured = os.environ.get(_BENCHMARK_RESULTS_DIRECTORY_ENV)
    return Path(configured) if configured else None


app = create_app(
    _dashboard_directory_from_environment(),
    _benchmark_results_directory_from_environment(),
)
