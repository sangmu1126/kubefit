from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from evaluator.cost import EvaluationResult, compare_request_costs
from evaluator.safety import evaluate_patch_eligibility


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
        _validate_evaluation_integrity(self.evaluation)
        return self


class AnalysisIntegrityCheck(BaseModel):
    code: Literal[
        "resource_values",
        "request_changes",
        "cost_comparison",
        "patch_eligibility",
    ]
    status: Literal["pass"] = "pass"
    reason: str


class AnalysisReview(BaseModel):
    schema_version: Literal[1] = 1
    verification_level: Literal["integrity_only"] = "integrity_only"
    target: AnalysisTarget
    workload_uid: str
    workload_created_at: datetime
    evaluation: EvaluationResult
    checks: list[AnalysisIntegrityCheck]
    limitations: list[str]


def review_analysis_artifact(artifact: AnalysisArtifact) -> AnalysisReview:
    return AnalysisReview(
        target=artifact.target,
        workload_uid=artifact.workload_uid,
        workload_created_at=artifact.workload_created_at,
        evaluation=artifact.evaluation,
        checks=[
            AnalysisIntegrityCheck(
                code="resource_values",
                reason=(
                    "recommended requests and limits are positive and limits are not "
                    "below requests"
                ),
            ),
            AnalysisIntegrityCheck(
                code="request_changes",
                reason="request change percentages match current and recommended resources",
            ),
            AnalysisIntegrityCheck(
                code="cost_comparison",
                reason="cost comparison was recomputed from resources and stored assumptions",
            ),
            AnalysisIntegrityCheck(
                code="patch_eligibility",
                reason="patch eligibility was recomputed from recommendation readiness and risk",
            ),
        ],
        limitations=[
            (
                "schema v1 does not retain raw observed usage, so the percentile "
                "recommendation cannot be recomputed"
            ),
            (
                "integrity validation does not authenticate the artifact producer or "
                "bind the artifact to repository bytes"
            ),
        ],
    )


def _validate_evaluation_integrity(evaluation: EvaluationResult) -> None:
    recommended = evaluation.recommendation.recommended
    values = (
        recommended.cpu_request_millicores,
        recommended.cpu_limit_millicores,
        recommended.memory_request_mib,
        recommended.memory_limit_mib,
    )
    if any(value <= 0 for value in values):
        raise ValueError("recommended resources must be positive")
    if recommended.cpu_limit_millicores < recommended.cpu_request_millicores:
        raise ValueError("recommended CPU limit must not be below its request")
    if recommended.memory_limit_mib < recommended.memory_request_mib:
        raise ValueError("recommended memory limit must not be below its request")

    expected_cpu_change = round(
        (recommended.cpu_request_millicores / evaluation.current.cpu_request_millicores - 1)
        * 100,
        1,
    )
    expected_memory_change = round(
        (recommended.memory_request_mib / evaluation.current.memory_request_mib - 1)
        * 100,
        1,
    )
    if evaluation.recommendation.cpu_request_change_percent != expected_cpu_change:
        raise ValueError("CPU request change percentage conflicts with resources")
    if evaluation.recommendation.memory_request_change_percent != expected_memory_change:
        raise ValueError("memory request change percentage conflicts with resources")

    expected_cost = compare_request_costs(
        evaluation.current,
        recommended,
        evaluation.cost.assumptions,
        evaluation.cost.replica_count,
    )
    if evaluation.cost != expected_cost:
        raise ValueError("cost comparison conflicts with resources or assumptions")

    expected_eligibility = evaluate_patch_eligibility(evaluation.recommendation)
    if evaluation.patch_eligibility != expected_eligibility:
        raise ValueError("patch eligibility conflicts with recommendation evidence")
