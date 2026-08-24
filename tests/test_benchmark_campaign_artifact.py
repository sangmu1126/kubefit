import json
from pathlib import Path

import pytest

from benchmarks import (
    BenchmarkCampaignEvidenceError,
    load_benchmark_campaign_evidence,
    load_benchmark_campaign_plan,
    write_benchmark_campaign_evidence,
    write_benchmark_campaign_plan,
)
from tests.test_benchmark_campaign import _campaign_pairs
from tests.test_benchmark_runner import published_proposal


def completed_campaign(tmp_path: Path, *, planned_pairs: int = 3):
    proposal = published_proposal(tmp_path)
    campaign = write_benchmark_campaign_plan(
        tmp_path / "campaigns",
        proposal.path,
        planned_pairs,
        b"fixed evidence seed",
    )
    plan = load_benchmark_campaign_plan(campaign.path)
    pairs = _campaign_pairs(tmp_path, proposal.path, plan)
    return proposal, campaign, pairs


def test_persists_self_contained_campaign_and_replays_every_pair(tmp_path: Path) -> None:
    proposal, campaign, pairs = completed_campaign(tmp_path)

    artifact = write_benchmark_campaign_evidence(
        tmp_path / "campaign-evidence", campaign.path, list(reversed(pairs))
    )
    loaded = load_benchmark_campaign_evidence(artifact.path)

    assert artifact.reused is False
    assert artifact.artifact_id == loaded.artifact_id
    assert artifact.campaign_id == loaded.campaign_id == campaign.campaign_id
    assert artifact.proposal_id == loaded.proposal_id == proposal.artifact_id
    assert artifact.pair_ids == loaded.completion.pair_ids
    assert loaded.completion.status == "complete"
    assert [pair.artifact_id for pair in loaded.pairs] == artifact.pair_ids
    assert len(artifact.files) == 68
    assert loaded.report_path.read_text().startswith(
        "# KubeFit completed benchmark campaign evidence\n"
    )


def test_reuses_only_byte_identical_campaign_evidence(tmp_path: Path) -> None:
    _, campaign, pairs = completed_campaign(tmp_path)
    output = tmp_path / "campaign-evidence"

    first = write_benchmark_campaign_evidence(output, campaign.path, pairs)
    second = write_benchmark_campaign_evidence(
        output, campaign.path, list(reversed(pairs))
    )

    assert second.artifact_id == first.artifact_id
    assert second.path == first.path
    assert second.reused is True


def test_rejects_tampered_embedded_pair_evidence(tmp_path: Path) -> None:
    _, campaign, pairs = completed_campaign(tmp_path)
    artifact = write_benchmark_campaign_evidence(
        tmp_path / "campaign-evidence", campaign.path, pairs
    )
    target = (
        artifact.path
        / "pairs"
        / artifact.pair_ids[0]
        / "assessment.json"
    )
    target.write_text("{}\n")

    with pytest.raises(BenchmarkCampaignEvidenceError, match="size changed"):
        load_benchmark_campaign_evidence(artifact.path)


def test_rejects_unsafe_pair_id_before_nested_path_resolution(tmp_path: Path) -> None:
    _, campaign, pairs = completed_campaign(tmp_path)
    artifact = write_benchmark_campaign_evidence(
        tmp_path / "campaign-evidence", campaign.path, pairs
    )
    index_path = artifact.path / "evidence.json"
    index = json.loads(index_path.read_text())
    index["pair_ids"][0] = "../../outside"
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")

    with pytest.raises(BenchmarkCampaignEvidenceError, match="index is invalid"):
        load_benchmark_campaign_evidence(artifact.path)


def test_does_not_persist_incomplete_campaign(tmp_path: Path) -> None:
    _, campaign, pairs = completed_campaign(tmp_path)
    output = tmp_path / "campaign-evidence"

    with pytest.raises(BenchmarkCampaignEvidenceError, match="got incomplete"):
        write_benchmark_campaign_evidence(output, campaign.path, pairs[:-1])

    assert not output.exists()
