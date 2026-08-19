from fastapi import FastAPI
from pydantic import BaseModel

from recommender import CurrentResources, ObservedUsage, ResourceRecommendation, recommend_resources

app = FastAPI(
    title="KubeFit API",
    version="0.1.0",
    description="Explainable Kubernetes resource recommendations with GitOps safety.",
)


class RecommendationRequest(BaseModel):
    current: CurrentResources
    observed: ObservedUsage


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/recommendations", response_model=ResourceRecommendation)
def create_recommendation(request: RecommendationRequest) -> ResourceRecommendation:
    return recommend_resources(request.current, request.observed)

