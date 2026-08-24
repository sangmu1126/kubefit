from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.campaign import BenchmarkCampaignCheck
from benchmarks.campaign_artifact import load_benchmark_campaign_evidence
from benchmarks.pair_artifact import LoadedCounterbalancedPair


class BenchmarkCampaignBlockReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    block: int = Field(ge=1, le=100)
    pair_id: Annotated[str, Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")]
    status: Literal["pass"] = "pass"
    scheduled_first_order: Literal["before-after", "after-before"]
    observed_first_order: Literal["before-after", "after-before"]
    measurement_started_at: datetime
    measurement_finished_at: datetime
    benchmark_ids: list[
        Annotated[str, Field(pattern=r"^benchmark-[0-9a-f]{32}$")]
    ] = Field(min_length=2, max_length=2)


class BenchmarkCampaignReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(
        pattern=r"^benchmark-campaign-evidence-[0-9a-f]{32}$"
    )
    campaign_id: str = Field(pattern=r"^benchmark-campaign-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    verification_level: Literal["campaign_full_artifact_replay"] = (
        "campaign_full_artifact_replay"
    )
    status: Literal["complete"] = "complete"
    planned_pairs: int = Field(ge=2, le=100)
    completed_pairs: int = Field(ge=2, le=100)
    stopping_rule: Literal["complete_all_planned_pairs"]
    aggregation_performed: Literal[False] = False
    blocks: list[BenchmarkCampaignBlockReview] = Field(min_length=2, max_length=100)
    checks: list[BenchmarkCampaignCheck]
    limitations: list[str]


def review_benchmark_campaign_evidence(path: Path) -> BenchmarkCampaignReview:
    """Replay a completed campaign and project chronological collection evidence."""
    loaded = load_benchmark_campaign_evidence(path)
    blocks = [
        _block_review(block.block, block.first_trial_order, pair)
        for block, pair in zip(loaded.plan.schedule, loaded.pairs, strict=True)
    ]
    return BenchmarkCampaignReview(
        artifact_id=loaded.artifact_id,
        campaign_id=loaded.campaign_id,
        proposal_id=loaded.proposal_id,
        planned_pairs=loaded.completion.planned_pairs,
        completed_pairs=loaded.completion.completed_pairs,
        stopping_rule=loaded.plan.stopping_rule,
        blocks=blocks,
        checks=loaded.completion.checks,
        limitations=loaded.completion.limitations,
    )


def _block_review(
    block: int,
    scheduled_first_order: Literal["before-after", "after-before"],
    pair: LoadedCounterbalancedPair,
) -> BenchmarkCampaignBlockReview:
    trials = [
        ("before-after", pair.before_after),
        ("after-before", pair.after_before),
    ]
    intervals = [
        (
            order,
            min(
                result.before.provenance.run_started_at,
                result.after.provenance.run_started_at,
            ),
            max(
                result.before.provenance.run_finished_at,
                result.after.provenance.run_finished_at,
            ),
        )
        for order, result in trials
    ]
    intervals.sort(key=lambda item: item[1])
    return BenchmarkCampaignBlockReview(
        block=block,
        pair_id=pair.artifact_id,
        scheduled_first_order=scheduled_first_order,
        observed_first_order=intervals[0][0],
        measurement_started_at=intervals[0][1],
        measurement_finished_at=intervals[1][2],
        benchmark_ids=sorted(
            [pair.before_after.artifact_id, pair.after_before.artifact_id]
        ),
    )
