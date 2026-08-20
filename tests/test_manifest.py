from decimal import Decimal
from pathlib import Path

import pytest

from evaluator import CostAssumptions, EvaluationResult, evaluate_resources
from gitops import (
    ManifestPatchError,
    ManifestSource,
    ManifestTarget,
    generate_resource_patch,
)
from recommender import CurrentResources, ObservedUsage

FIXTURES = Path(__file__).parent / "fixtures" / "manifest"


def eligible_evaluation() -> EvaluationResult:
    return evaluate_resources(
        CurrentResources(
            cpu_request_millicores=1000,
            cpu_limit_millicores=2000,
            memory_request_mib=2048,
            memory_limit_mib=4096,
        ),
        ObservedUsage(
            cpu_p95_millicores=230,
            memory_p99_mib=710,
            cpu_max_millicores=400,
            memory_max_mib=900,
            sample_count=1900,
            observation_coverage=0.95,
            desired_replicas=2,
            available_replicas=2,
            observed_replicas=2,
            cpu_throttling_p95_percent=0.2,
            cpu_throttling_max_percent=0.5,
            cpu_throttling_sample_count=1900,
            cpu_throttling_pod_count=2,
            cpu_throttling_observation_coverage=0.95,
            container_status_count=2,
            restart_count=0,
            oom_killed_count=0,
        ),
        CostAssumptions(
            cpu_core_hour_usd=Decimal("0.04"),
            memory_gib_hour_usd=Decimal("0.005"),
            price_source="example://test",
        ),
        replica_count=2,
    )


def target() -> ManifestTarget:
    return ManifestTarget(namespace="demo", deployment="demo", container="api")


def test_generates_golden_minimal_patch_without_reformatting() -> None:
    original = (FIXTURES / "input.yaml").read_text()
    expected = (FIXTURES / "expected.yaml").read_text()

    result = generate_resource_patch(
        [ManifestSource(path="deploy/demo.yaml", content=original)],
        target(),
        eligible_evaluation(),
    )

    assert result.patched_content == expected
    assert result.unified_diff.startswith("--- a/deploy/demo.yaml\n+++ b/deploy/demo.yaml\n")
    assert result.unified_diff.count("-              ") == 4
    assert result.unified_diff.count("+              ") == 4
    assert result.report.source_path == "deploy/demo.yaml"
    assert result.report.document_index == 2
    assert result.report.original_sha256
    assert [change.field for change in result.report.changes] == [
        "requests.cpu",
        "requests.memory",
        "limits.cpu",
        "limits.memory",
    ]
    assert result.report.recommendation_evidence


def test_carries_eligibility_warning_into_patch_report() -> None:
    original = (FIXTURES / "input.yaml").read_text()
    evaluation = eligible_evaluation()
    evaluation.patch_eligibility.warnings = ["review medium headroom"]

    result = generate_resource_patch(
        [ManifestSource(path="deploy/demo.yaml", content=original)],
        target(),
        evaluation,
    )

    assert result.report.eligibility_warnings == ["review medium headroom"]


def test_rejects_blocked_evaluation_before_parsing_yaml() -> None:
    evaluation = eligible_evaluation()
    evaluation.patch_eligibility.status = "blocked"
    evaluation.patch_eligibility.blocking_reasons = ["test block"]

    with pytest.raises(ManifestPatchError, match="test block"):
        generate_resource_patch(
            [ManifestSource(path="broken.yaml", content="not: [valid")],
            target(),
            evaluation,
        )


def test_rejects_stale_manifest_resources() -> None:
    original = (FIXTURES / "input.yaml").read_text().replace('cpu: "1000m"', 'cpu: "900m"')

    with pytest.raises(ManifestPatchError, match="stale"):
        generate_resource_patch(
            [ManifestSource(path="deploy/demo.yaml", content=original)],
            target(),
            eligible_evaluation(),
        )


def test_rejects_target_found_in_multiple_sources() -> None:
    original = (FIXTURES / "input.yaml").read_text()

    with pytest.raises(ManifestPatchError, match="ambiguous"):
        generate_resource_patch(
            [
                ManifestSource(path="deploy/a.yaml", content=original),
                ManifestSource(path="deploy/b.yaml", content=original),
            ],
            target(),
            eligible_evaluation(),
        )


def test_rejects_missing_target_container() -> None:
    original = (FIXTURES / "input.yaml").read_text()

    with pytest.raises(ManifestPatchError, match="container 'worker' was not found"):
        generate_resource_patch(
            [ManifestSource(path="deploy/demo.yaml", content=original)],
            ManifestTarget(namespace="demo", deployment="demo", container="worker"),
            eligible_evaluation(),
        )


def test_rejects_duplicate_deployment_even_if_only_one_has_target_container() -> None:
    original = (FIXTURES / "input.yaml").read_text()
    without_api = original.replace("- name: api", "- name: worker")

    with pytest.raises(ManifestPatchError, match="ambiguous"):
        generate_resource_patch(
            [
                ManifestSource(path="deploy/a.yaml", content=original),
                ManifestSource(path="deploy/b.yaml", content=without_api),
            ],
            target(),
            eligible_evaluation(),
        )


def test_preserves_semantically_unchanged_resource_scalar() -> None:
    original = (FIXTURES / "input.yaml").read_text().replace(
        'cpu: "1000m"', 'cpu: "1"'
    )
    evaluation = eligible_evaluation()
    evaluation.recommendation.recommended.cpu_request_millicores = 1000

    result = generate_resource_patch(
        [ManifestSource(path="deploy/demo.yaml", content=original)],
        target(),
        evaluation,
    )

    assert 'cpu: "1"' in result.patched_content
    assert all(change.field != "requests.cpu" for change in result.report.changes)


def test_rejects_invalid_resource_value() -> None:
    original = (FIXTURES / "input.yaml").read_text().replace(
        'cpu: "1000m"', 'cpu: "invalid"'
    )

    with pytest.raises(ManifestPatchError, match="invalid resource values"):
        generate_resource_patch(
            [ManifestSource(path="deploy/demo.yaml", content=original)],
            target(),
            eligible_evaluation(),
        )


def test_rejects_duplicate_resource_field() -> None:
    original = (FIXTURES / "input.yaml").read_text().replace(
        'cpu: "1000m"', 'cpu: "1000m"\n              cpu: "1000m"'
    )

    with pytest.raises(ManifestPatchError, match="duplicate field 'cpu'"):
        generate_resource_patch(
            [ManifestSource(path="deploy/demo.yaml", content=original)],
            target(),
            eligible_evaluation(),
        )


def test_rejects_aliased_resource_value() -> None:
    original = (
        (FIXTURES / "input.yaml")
        .read_text()
        .replace(
            "---\n# This comment",
            '---\nx-cpu: &cpu "1000m"\n# This comment',
        )
        .replace('cpu: "1000m"', "cpu: *cpu")
    )

    with pytest.raises(ManifestPatchError, match="local scalar"):
        generate_resource_patch(
            [ManifestSource(path="deploy/demo.yaml", content=original)],
            target(),
            eligible_evaluation(),
        )


def test_rejects_malformed_yaml() -> None:
    with pytest.raises(ManifestPatchError, match="invalid YAML"):
        generate_resource_patch(
            [ManifestSource(path="broken.yaml", content="apiVersion: [")],
            target(),
            eligible_evaluation(),
        )


@pytest.mark.parametrize("path", ["", "/tmp/demo.yaml", "../demo.yaml", "bad\npath.yaml"])
def test_rejects_unsafe_manifest_source_path(path: str) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        ManifestSource(path=path, content="")
