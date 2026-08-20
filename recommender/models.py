from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CurrentResources(BaseModel):
    cpu_request_millicores: int = Field(gt=0)
    cpu_limit_millicores: int = Field(gt=0)
    memory_request_mib: int = Field(gt=0)
    memory_limit_mib: int = Field(gt=0)

    @model_validator(mode="after")
    def limits_are_not_below_requests(self) -> "CurrentResources":
        if self.cpu_limit_millicores < self.cpu_request_millicores:
            raise ValueError("CPU limit must be greater than or equal to its request")
        if self.memory_limit_mib < self.memory_request_mib:
            raise ValueError("memory limit must be greater than or equal to its request")
        return self


class ObservedUsage(BaseModel):
    cpu_p95_millicores: float = Field(ge=0)
    memory_p99_mib: float = Field(ge=0)
    cpu_max_millicores: float | None = Field(default=None, ge=0)
    memory_max_mib: float | None = Field(default=None, ge=0)
    observation_days: int = Field(default=7, ge=1)
    step_seconds: int | None = Field(default=None, ge=1)
    sample_count: int | None = Field(default=None, ge=0)
    observation_coverage: float | None = Field(default=None, ge=0, le=1)
    desired_replicas: int | None = Field(default=None, ge=1)
    available_replicas: int | None = Field(default=None, ge=0)
    observed_replicas: int | None = Field(default=None, ge=0)
    metric_pod_count: int | None = Field(default=None, ge=0)
    workload_uid: str | None = None
    workload_created_at: datetime | None = None
    history_clipped: bool = False

    @model_validator(mode="after")
    def maxima_are_not_below_percentiles(self) -> "ObservedUsage":
        if (
            self.cpu_max_millicores is not None
            and self.cpu_max_millicores < self.cpu_p95_millicores
        ):
            raise ValueError("CPU maximum must be greater than or equal to P95")
        if self.memory_max_mib is not None and self.memory_max_mib < self.memory_p99_mib:
            raise ValueError("memory maximum must be greater than or equal to P99")
        return self


class ResourceValues(BaseModel):
    cpu_request_millicores: int
    cpu_limit_millicores: int
    memory_request_mib: int
    memory_limit_mib: int


class RiskAssessment(BaseModel):
    oom: Literal["low", "medium", "high", "unknown"]
    cpu_throttling: Literal["low", "medium", "high", "unknown"]
    reasons: list[str]


class RecommendationReadiness(BaseModel):
    status: Literal["ready", "insufficient_data"]
    reasons: list[str]


class ResourceRecommendation(BaseModel):
    recommended: ResourceValues
    cpu_request_change_percent: float
    memory_request_change_percent: float
    readiness: RecommendationReadiness
    risk: RiskAssessment
    evidence: list[str]
