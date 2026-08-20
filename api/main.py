from fastapi import FastAPI
from pydantic import BaseModel, Field

from evaluator import CostAssumptions, EvaluationResult, evaluate_resources
from recommender import CurrentResources, ObservedUsage, ResourceRecommendation, recommend_resources

app = FastAPI(
    title="KubeFit API",
    version="0.1.0",
    description="Explainable Kubernetes resource recommendations with GitOps safety.",
)


class RecommendationRequest(BaseModel):
    current: CurrentResources
    observed: ObservedUsage


class EvaluationRequest(BaseModel):
    current: CurrentResources
    observed: ObservedUsage
    cost_assumptions: CostAssumptions
    replica_count: int = Field(gt=0)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/recommendations", response_model=ResourceRecommendation)
def create_recommendation(request: RecommendationRequest) -> ResourceRecommendation:
    return recommend_resources(request.current, request.observed)


@app.post("/v1/evaluations", response_model=EvaluationResult)
def create_evaluation(request: EvaluationRequest) -> EvaluationResult:
    return evaluate_resources(
        request.current,
        request.observed,
        request.cost_assumptions,
        request.replica_count,
    )
