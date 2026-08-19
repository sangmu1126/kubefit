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
    observation_days: int = Field(default=7, ge=1)


class ResourceValues(BaseModel):
    cpu_request_millicores: int
    cpu_limit_millicores: int
    memory_request_mib: int
    memory_limit_mib: int


class RiskAssessment(BaseModel):
    oom: Literal["low", "medium", "high"]
    cpu_throttling: Literal["low", "medium", "high"]
    reasons: list[str]


class ResourceRecommendation(BaseModel):
    recommended: ResourceValues
    estimated_request_reduction_percent: float
    risk: RiskAssessment
    evidence: list[str]
