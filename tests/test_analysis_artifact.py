from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evaluator import AnalysisArtifact, AnalysisTarget, review_analysis_artifact
from tests.test_manifest import eligible_evaluation


def test_binds_evaluation_to_workload_identity() -> None:
    result = AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    )

    restored = AnalysisArtifact.model_validate_json(result.model_dump_json())

    assert restored.target.deployment == "api"
    assert restored.workload_uid == "deployment-uid"
    assert restored.evaluation == result.evaluation


def test_rejects_naive_workload_creation_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        AnalysisArtifact(
            target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
            workload_uid="deployment-uid",
            workload_created_at=datetime(2026, 8, 21),
            evaluation=eligible_evaluation(),
        )


def test_review_recomputes_integrity_checks_and_states_v1_limitations() -> None:
    artifact = AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    )

    review = review_analysis_artifact(artifact)

    assert review.verification_level == "integrity_only"
    assert [check.code for check in review.checks] == [
        "resource_values",
        "request_changes",
        "cost_comparison",
        "patch_eligibility",
    ]
    assert all(check.status == "pass" for check in review.checks)
    assert "raw observed usage" in review.limitations[0]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("recommendation", "cpu_request_change_percent"), -99.0, "change percentage"),
        (("cost", "savings_percent"), "99.9", "cost comparison"),
        (("patch_eligibility", "status"), "blocked", "patch eligibility"),
    ],
)
def test_rejects_internally_conflicting_evaluation(
    path: tuple[str, str], value: object, message: str
) -> None:
    evaluation = eligible_evaluation().model_dump(mode="json")
    evaluation[path[0]][path[1]] = value

    with pytest.raises(ValidationError, match=message):
        AnalysisArtifact.model_validate(
            {
                "target": {"namespace": "demo", "deployment": "api", "container": "api"},
                "workload_uid": "deployment-uid",
                "workload_created_at": "2026-08-21T00:00:00Z",
                "evaluation": evaluation,
            }
        )
