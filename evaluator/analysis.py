from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from evaluator.cost import EvaluationResult


class AnalysisTarget(BaseModel):
    namespace: str = Field(min_length=1)
    deployment: str = Field(min_length=1)
    container: str = Field(min_length=1)


class AnalysisArtifact(BaseModel):
    schema_version: Literal[1] = 1
    target: AnalysisTarget
    workload_uid: str = Field(min_length=1)
    workload_created_at: datetime
    evaluation: EvaluationResult

    @model_validator(mode="after")
    def creation_time_has_timezone(self) -> "AnalysisArtifact":
        if self.workload_created_at.tzinfo is None:
            raise ValueError("workload creation timestamp must include timezone")
        return self
