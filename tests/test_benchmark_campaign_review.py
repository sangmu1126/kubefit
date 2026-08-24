from pathlib import Path

from benchmarks import (
    review_benchmark_campaign_evidence,
    write_benchmark_campaign_evidence,
)
from tests.test_benchmark_campaign_artifact import completed_campaign


def test_reviews_complete_campaign_as_chronological_blocks_without_aggregation(
    tmp_path: Path,
) -> None:
    proposal, campaign, pairs = completed_campaign(tmp_path, planned_pairs=3)
    evidence = write_benchmark_campaign_evidence(
        tmp_path / "campaign-evidence", campaign.path, pairs
    )

    review = review_benchmark_campaign_evidence(evidence.path)

    assert review.artifact_id == evidence.artifact_id
    assert review.campaign_id == campaign.campaign_id
    assert review.proposal_id == proposal.artifact_id
    assert review.verification_level == "campaign_full_artifact_replay"
    assert review.status == "complete"
    assert review.completed_pairs == review.planned_pairs == 3
    assert review.aggregation_performed is False
    assert [block.block for block in review.blocks] == [1, 2, 3]
    assert [block.pair_id for block in review.blocks] == evidence.pair_ids
    assert all(
        block.scheduled_first_order == block.observed_first_order
        for block in review.blocks
    )
    assert all(block.status == "pass" for block in review.blocks)
    assert all(
        current.measurement_finished_at <= following.measurement_started_at
        for current, following in zip(review.blocks, review.blocks[1:], strict=False)
    )
    assert any("statistical significance" in item for item in review.limitations)
