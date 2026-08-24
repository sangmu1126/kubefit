from datetime import timedelta
from pathlib import Path

import pytest

from benchmarks import (
    BenchmarkCampaignError,
    assess_benchmark_campaign,
    create_benchmark_campaign_plan,
    execute_benchmark,
    load_benchmark_campaign_plan,
    write_benchmark_campaign_plan,
    write_benchmark_result,
    write_counterbalanced_pair,
)
from tests.test_benchmark_pair import _shift_run
from tests.test_benchmark_runner import RecordingController, collector, published_proposal


def test_plan_is_balanced_deterministic_immutable_and_seed_secret(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    seed = b"fixed campaign seed"

    artifact = write_benchmark_campaign_plan(
        tmp_path / "campaigns", proposal.path, 5, seed
    )
    reused = write_benchmark_campaign_plan(
        tmp_path / "campaigns", proposal.path, 5, seed
    )
    plan = load_benchmark_campaign_plan(artifact.path)

    assert reused.reused is True
    assert reused.campaign_id == artifact.campaign_id == plan.campaign_id
    assert plan.planned_pairs == 5
    assert plan.stopping_rule == "complete_all_planned_pairs"
    assert abs(
        sum(block.first_trial_order == "before-after" for block in plan.schedule)
        - sum(block.first_trial_order == "after-before" for block in plan.schedule)
    ) == 1
    assert seed not in (artifact.path / "campaign.json").read_bytes()
    assert len(list(artifact.path.iterdir())) == 2


def test_plan_identity_changes_with_seed_and_rejects_too_few_pairs() -> None:
    first = create_benchmark_campaign_plan(
        "proposal-" + "a" * 32, 4, b"campaign-seed-one"
    )
    second = create_benchmark_campaign_plan(
        "proposal-" + "a" * 32, 4, b"campaign-seed-two"
    )

    assert first.campaign_id != second.campaign_id
    with pytest.raises(BenchmarkCampaignError, match="between 2 and 100"):
        create_benchmark_campaign_plan("proposal-" + "a" * 32, 1, b"long-enough-seed")
    with pytest.raises(BenchmarkCampaignError, match="16 to 4096 bytes"):
        create_benchmark_campaign_plan("proposal-" + "a" * 32, 2, b"short")


def test_loader_rejects_tampered_campaign_report(tmp_path: Path) -> None:
    proposal = published_proposal(tmp_path)
    artifact = write_benchmark_campaign_plan(
        tmp_path / "campaigns", proposal.path, 3, b"long-enough-seed"
    )
    (artifact.path / "report.md").write_text("changed\n")

    with pytest.raises(BenchmarkCampaignError, match="report does not replay"):
        load_benchmark_campaign_plan(artifact.path)


def test_campaign_completion_requires_every_preregistered_chronological_block(
    tmp_path: Path,
) -> None:
    proposal = published_proposal(tmp_path)
    campaign = write_benchmark_campaign_plan(
        tmp_path / "campaigns", proposal.path, 3, b"fixed schedule seed"
    )
    plan = load_benchmark_campaign_plan(campaign.path)
    pairs = _campaign_pairs(tmp_path, proposal.path, plan)

    incomplete = assess_benchmark_campaign(campaign.path, pairs[:2])
    complete = assess_benchmark_campaign(campaign.path, list(reversed(pairs)))

    assert incomplete.status == "incomplete"
    assert incomplete.remaining_blocks == [3]
    assert next(
        check.status for check in incomplete.checks if check.code == "fixed_pair_count"
    ) == "incomplete"
    assert complete.status == "complete"
    assert complete.completed_pairs == 3
    assert complete.remaining_blocks == []
    assert {check.status for check in complete.checks} == {"pass"}
    assert "does not compute an aggregate treatment effect" in complete.limitations[2]


def test_campaign_rejects_execution_that_violates_randomized_schedule(
    tmp_path: Path,
) -> None:
    proposal = published_proposal(tmp_path)
    campaign = write_benchmark_campaign_plan(
        tmp_path / "campaigns", proposal.path, 3, b"fixed schedule seed"
    )
    plan = load_benchmark_campaign_plan(campaign.path)
    pairs = _campaign_pairs(tmp_path, proposal.path, plan, flip_block=2)

    completion = assess_benchmark_campaign(campaign.path, pairs)

    assert completion.status == "invalid"
    assert next(
        check.status for check in completion.checks if check.code == "randomized_schedule"
    ) == "invalid"


def _campaign_pairs(tmp_path: Path, proposal_path: Path, plan, flip_block: int | None = None):
    pairs = []
    for block in plan.schedule:
        runs = {}
        for order in ("before-after", "after-before"):
            controller = RecordingController()
            runs[order] = execute_benchmark(
                proposal_path,
                controller,
                collector(controller.events),
                execution_order=order,
            )
        first_order = block.first_trial_order
        if block.block == flip_block:
            first_order = (
                "after-before" if first_order == "before-after" else "before-after"
            )
        second_order = (
            "after-before" if first_order == "before-after" else "before-after"
        )
        block_offset = timedelta(hours=block.block)
        shifted = {
            first_order: _shift_run(runs[first_order], block_offset),
            second_order: _shift_run(
                runs[second_order], block_offset + timedelta(minutes=15)
            ),
        }
        artifacts = [
            write_benchmark_result(tmp_path / "results", shifted[order])
            for order in ("before-after", "after-before")
        ]
        pairs.append(
            write_counterbalanced_pair(
                tmp_path / "pairs", artifacts[0].path, artifacts[1].path
            ).path
        )
    return pairs
