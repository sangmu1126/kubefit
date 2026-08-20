from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evaluator import AnalysisArtifact, AnalysisTarget
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
