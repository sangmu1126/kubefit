from pathlib import Path

import pytest

from benchmarks import (
    CounterbalancedPairArtifactError,
    load_counterbalanced_pair,
    write_counterbalanced_pair,
)
from tests.test_benchmark_pair import published_pair


def test_persists_self_contained_pair_and_replays_embedded_trials(
    tmp_path: Path,
) -> None:
    proposal, trials = published_pair(tmp_path)

    artifact = write_counterbalanced_pair(
        tmp_path / "pairs", trials[0].path, trials[1].path
    )
    loaded = load_counterbalanced_pair(artifact.path)

    assert artifact.reused is False
    assert artifact.artifact_id == loaded.assessment.assessment_id
    assert artifact.proposal_id == proposal.artifact_id == loaded.proposal_id
    assert artifact.benchmark_ids == sorted(
        [loaded.before_after.artifact_id, loaded.after_before.artifact_id]
    )
    assert loaded.assessment.status == "pass"
    assert loaded.report_path.read_text().startswith(
        "# KubeFit counterbalanced benchmark pair\n"
    )
    assert len(artifact.files) == 21


def publication_artifacts(tmp_path: Path):
    proposal, trials = published_pair(tmp_path)
    pair = write_counterbalanced_pair(
        tmp_path / "pairs", trials[0].path, trials[1].path
    )
    return proposal, trials[0], pair


def test_reuses_only_byte_identical_pair_artifact(tmp_path: Path) -> None:
    _, trials = published_pair(tmp_path)
    first = write_counterbalanced_pair(
        tmp_path / "pairs", trials[0].path, trials[1].path
    )

    second = write_counterbalanced_pair(
        tmp_path / "pairs", trials[1].path, trials[0].path
    )

    assert second.artifact_id == first.artifact_id
    assert second.path == first.path
    assert second.reused is True


def test_rejects_tampered_embedded_benchmark(tmp_path: Path) -> None:
    _, trials = published_pair(tmp_path)
    artifact = write_counterbalanced_pair(
        tmp_path / "pairs", trials[0].path, trials[1].path
    )
    target = artifact.path / "trials" / artifact.benchmark_ids[0] / "verdict.json"
    target.write_text("{}\n")

    with pytest.raises(CounterbalancedPairArtifactError, match="size changed"):
        load_counterbalanced_pair(artifact.path)


def test_does_not_persist_non_counterbalanced_input(tmp_path: Path) -> None:
    _, trials = published_pair(tmp_path)
    output = tmp_path / "pairs"

    with pytest.raises(CounterbalancedPairArtifactError, match="got invalid"):
        write_counterbalanced_pair(output, trials[0].path, trials[0].path)

    assert not output.exists()
