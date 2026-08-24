from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.pair_artifact import LoadedCounterbalancedPair, load_counterbalanced_pair
from gitops.bundle import load_proposal_bundle


class BenchmarkCampaignError(RuntimeError):
    """Raised when a preregistered campaign artifact is unsafe or inconsistent."""


class BenchmarkCampaignBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    block: int = Field(ge=1)
    first_trial_order: Literal["before-after", "after-before"]
    second_trial_order: Literal["before-after", "after-before"]


class BenchmarkCampaignPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    campaign_id: str = Field(pattern=r"^benchmark-campaign-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    planned_pairs: int = Field(ge=2, le=100)
    randomization_seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stopping_rule: Literal["complete_all_planned_pairs"] = "complete_all_planned_pairs"
    schedule: list[BenchmarkCampaignBlock]
    limitations: list[str]


class BenchmarkCampaignArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    campaign_id: str = Field(pattern=r"^benchmark-campaign-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    path: Path
    reused: bool


class BenchmarkCampaignCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    status: Literal["pass", "incomplete", "invalid"]
    reason: str


class BenchmarkCampaignCompletion(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    campaign_id: str = Field(pattern=r"^benchmark-campaign-[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    status: Literal["complete", "incomplete", "invalid"]
    planned_pairs: int
    completed_pairs: int
    remaining_blocks: list[int]
    pair_ids: list[Annotated[str, Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")]]
    checks: list[BenchmarkCampaignCheck]
    invalid_reasons: list[str]
    limitations: list[str]


_LIMITATIONS = [
    (
        "two or more planned pairs establish replication but do not by themselves "
        "justify a confidence interval or statistical significance claim"
    ),
    (
        "the schedule balances which within-pair execution order starts first; it "
        "cannot randomize uncontrolled cluster or traffic conditions"
    ),
    (
        "completion verifies preregistered collection discipline and does not compute "
        "an aggregate treatment effect"
    ),
    (
        "the retained seed hash commits to the seed but does not prove that the seed "
        "was generated randomly or kept free from outcome-based selection"
    ),
]


def write_benchmark_campaign_plan(
    output_root: Path,
    proposal_path: Path,
    planned_pairs: int,
    randomization_seed: bytes,
) -> BenchmarkCampaignArtifact:
    proposal = load_proposal_bundle(proposal_path)
    plan = create_benchmark_campaign_plan(
        proposal.artifact_id, planned_pairs, randomization_seed
    )
    payloads = _campaign_payloads(plan)
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final_path = output_root / plan.campaign_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            _validate_existing(final_path, payloads)
            return BenchmarkCampaignArtifact(
                campaign_id=plan.campaign_id,
                proposal_id=plan.proposal_id,
                path=final_path,
                reused=True,
            )
        staging = Path(tempfile.mkdtemp(prefix=f".{plan.campaign_id}-", dir=output_root))
        staging.chmod(0o700)
        for name, content in sorted(payloads.items()):
            _write_file(staging / name, content)
        _fsync_directory(staging)
        os.rename(staging, final_path)
        staging = None
        _fsync_directory(output_root)
        return BenchmarkCampaignArtifact(
            campaign_id=plan.campaign_id,
            proposal_id=plan.proposal_id,
            path=final_path,
            reused=False,
        )
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
        _fsync_directory(output_root)


def create_benchmark_campaign_plan(
    proposal_id: str,
    planned_pairs: int,
    randomization_seed: bytes,
) -> BenchmarkCampaignPlan:
    if not 2 <= planned_pairs <= 100:
        raise BenchmarkCampaignError("planned pairs must be between 2 and 100")
    if not 16 <= len(randomization_seed) <= 4096:
        raise BenchmarkCampaignError("randomization seed must contain 16 to 4096 bytes")
    seed_digest = hashlib.sha256(randomization_seed).hexdigest()
    first_orders = [
        "before-after" if index < (planned_pairs + 1) // 2 else "after-before"
        for index in range(planned_pairs)
    ]
    _deterministic_shuffle(first_orders, randomization_seed, proposal_id)
    schedule = [
        {
            "block": index,
            "first_trial_order": first,
            "second_trial_order": (
                "after-before" if first == "before-after" else "before-after"
            ),
        }
        for index, first in enumerate(first_orders, start=1)
    ]
    identity = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "planned_pairs": planned_pairs,
        "randomization_seed_sha256": seed_digest,
        "stopping_rule": "complete_all_planned_pairs",
        "schedule": schedule,
        "limitations": _LIMITATIONS,
    }
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return BenchmarkCampaignPlan(
        campaign_id=f"benchmark-campaign-{digest[:32]}",
        **identity,
    )


def load_benchmark_campaign_plan(path: Path) -> BenchmarkCampaignPlan:
    if path.is_symlink() or not path.is_dir():
        raise BenchmarkCampaignError("campaign path must be a regular directory")
    actual = sorted(item.name for item in path.iterdir() if item.is_file())
    if actual != ["campaign.json", "report.md"] or any(
        item.is_symlink() or not item.is_file() for item in path.iterdir()
    ):
        raise BenchmarkCampaignError("campaign artifact file set is invalid")
    try:
        campaign_bytes = (path / "campaign.json").read_bytes()
        raw = json.loads(campaign_bytes)
        plan = BenchmarkCampaignPlan.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise BenchmarkCampaignError("campaign plan is invalid") from exc
    if campaign_bytes != _canonical_json(raw):
        raise BenchmarkCampaignError("campaign plan is not canonical JSON")
    if path.name != plan.campaign_id:
        raise BenchmarkCampaignError("campaign directory does not match campaign ID")
    identity = plan.model_dump(mode="json", exclude={"campaign_id"})
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    if plan.campaign_id != f"benchmark-campaign-{digest[:32]}":
        raise BenchmarkCampaignError("campaign identity does not replay")
    # The raw seed is deliberately not retained. The content-addressed identity freezes
    # the generated schedule, while this check verifies its balance and pair structure.
    _validate_schedule(plan)
    if (path / "report.md").read_text() != _render_campaign(plan):
        raise BenchmarkCampaignError("campaign report does not replay")
    return plan


def assess_benchmark_campaign(
    plan_path: Path,
    pair_paths: list[Path],
) -> BenchmarkCampaignCompletion:
    plan = load_benchmark_campaign_plan(plan_path)
    try:
        pairs = [load_counterbalanced_pair(path) for path in pair_paths]
    except (OSError, RuntimeError, ValueError) as exc:
        raise BenchmarkCampaignError("campaign requires valid pair artifacts") from exc
    pairs.sort(key=lambda pair: _pair_interval(pair)[0])
    checks: list[BenchmarkCampaignCheck] = []

    count_status = (
        "pass"
        if len(pairs) == plan.planned_pairs
        else "incomplete"
        if len(pairs) < plan.planned_pairs
        else "invalid"
    )
    checks.append(
        BenchmarkCampaignCheck(
            code="fixed_pair_count",
            status=count_status,
            reason=(
                f"received {len(pairs)} of {plan.planned_pairs} preregistered pairs"
                if count_status != "pass"
                else "all preregistered pairs are present"
            ),
        )
    )
    pair_ids = [pair.artifact_id for pair in pairs]
    benchmark_ids = [
        result.artifact_id
        for pair in pairs
        for result in (pair.before_after, pair.after_before)
    ]
    _append_boolean_check(
        checks,
        "unique_evidence",
        len(pair_ids) == len(set(pair_ids))
        and len(benchmark_ids) == len(set(benchmark_ids)),
        "every pair and embedded benchmark must be unique",
    )
    _append_boolean_check(
        checks,
        "proposal_binding",
        all(pair.proposal_id == plan.proposal_id for pair in pairs),
        "every pair must reference the preregistered proposal",
    )
    _append_boolean_check(
        checks,
        "profile_and_cost_binding",
        _same_profile_and_cost(pairs),
        "all campaign pairs must use one profile and identical request-cost bases",
    )
    intervals = [_pair_interval(pair) for pair in pairs]
    chronological = all(
        current[1] <= following[0]
        for current, following in zip(intervals, intervals[1:], strict=False)
    ) and all(start < finish for start, finish, _ in intervals)
    _append_boolean_check(
        checks,
        "chronological_blocks",
        chronological,
        "pair blocks and their two trials must not overlap",
    )
    schedule_matches = len(pairs) <= plan.planned_pairs and all(
        actual[2] == expected.first_trial_order
        for actual, expected in zip(intervals, plan.schedule, strict=False)
    )
    _append_boolean_check(
        checks,
        "randomized_schedule",
        schedule_matches,
        "observed first-trial orders must follow the preregistered schedule",
    )
    invalid_reasons = [check.reason for check in checks if check.status == "invalid"]
    status = (
        "invalid"
        if invalid_reasons
        else "complete"
        if len(pairs) == plan.planned_pairs
        else "incomplete"
    )
    return _completion(plan, pairs, checks, status)


def _completion(plan, pairs, checks, status) -> BenchmarkCampaignCompletion:
    invalid_reasons = [check.reason for check in checks if check.status == "invalid"]
    return BenchmarkCampaignCompletion(
        campaign_id=plan.campaign_id,
        proposal_id=plan.proposal_id,
        status=status,
        planned_pairs=plan.planned_pairs,
        completed_pairs=len(pairs),
        remaining_blocks=list(range(len(pairs) + 1, plan.planned_pairs + 1)),
        pair_ids=[pair.artifact_id for pair in pairs],
        checks=checks,
        invalid_reasons=invalid_reasons,
        limitations=_LIMITATIONS,
    )


def _pair_interval(
    pair: LoadedCounterbalancedPair,
) -> tuple[datetime, datetime, Literal["before-after", "after-before"]]:
    trials = [
        ("before-after", pair.before_after),
        ("after-before", pair.after_before),
    ]
    intervals = [
        (
            order,
            min(result.before.provenance.run_started_at, result.after.provenance.run_started_at),
            max(result.before.provenance.run_finished_at, result.after.provenance.run_finished_at),
        )
        for order, result in trials
    ]
    intervals.sort(key=lambda item: item[1])
    if intervals[0][2] > intervals[1][1]:
        return intervals[0][1], intervals[0][1], intervals[0][0]
    return intervals[0][1], intervals[1][2], intervals[0][0]


def _same_profile_and_cost(pairs: list[LoadedCounterbalancedPair]) -> bool:
    if not pairs:
        return True
    reference = pairs[0].before_after
    return all(
        result.before.profile_version == reference.before.profile_version
        and result.after.profile_version == reference.after.profile_version
        and result.before.request_cost_usd == reference.before.request_cost_usd
        and result.after.request_cost_usd == reference.after.request_cost_usd
        for pair in pairs
        for result in (pair.before_after, pair.after_before)
    )


def _append_boolean_check(checks, code, valid, invalid_reason) -> None:
    checks.append(
        BenchmarkCampaignCheck(
            code=code,
            status="pass" if valid else "invalid",
            reason=f"{code} is valid" if valid else invalid_reason,
        )
    )


def _validate_schedule(plan: BenchmarkCampaignPlan) -> None:
    if [block.block for block in plan.schedule] != list(range(1, plan.planned_pairs + 1)):
        raise BenchmarkCampaignError("campaign schedule blocks are not contiguous")
    if len(plan.schedule) != plan.planned_pairs or any(
        block.first_trial_order == block.second_trial_order for block in plan.schedule
    ):
        raise BenchmarkCampaignError("campaign schedule is not paired")
    counts = {
        order: sum(block.first_trial_order == order for block in plan.schedule)
        for order in ("before-after", "after-before")
    }
    if abs(counts["before-after"] - counts["after-before"]) > 1:
        raise BenchmarkCampaignError("campaign first-trial schedule is not balanced")


def _deterministic_shuffle(values: list[str], seed: bytes, proposal_id: str) -> None:
    for index in range(len(values) - 1, 0, -1):
        digest = hashlib.sha256(
            seed + b"\0" + proposal_id.encode() + b"\0" + str(index).encode()
        ).digest()
        selected = int.from_bytes(digest, "big") % (index + 1)
        values[index], values[selected] = values[selected], values[index]


def _campaign_payloads(plan: BenchmarkCampaignPlan) -> dict[str, bytes]:
    return {
        "campaign.json": _canonical_json(plan.model_dump(mode="json")),
        "report.md": _render_campaign(plan).encode(),
    }


def _render_campaign(plan: BenchmarkCampaignPlan) -> str:
    lines = [
        "# KubeFit preregistered benchmark campaign",
        "",
        f"- Campaign: `{plan.campaign_id}`",
        f"- Proposal: `{plan.proposal_id}`",
        f"- Planned pairs: `{plan.planned_pairs}`",
        f"- Stopping rule: `{plan.stopping_rule}`",
        f"- Randomization seed SHA-256: `{plan.randomization_seed_sha256}`",
        "",
        "## Schedule",
        "",
        "| Block | First trial | Second trial |",
        "|---:|---|---|",
    ]
    lines.extend(
        f"| {block.block} | `{block.first_trial_order}` | `{block.second_trial_order}` |"
        for block in plan.schedule
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in plan.limitations)
    return "\n".join(lines) + "\n"


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _validate_output_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise BenchmarkCampaignError("campaign output root must be a regular directory")


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BenchmarkCampaignError("another campaign publication holds the lock") from exc


def _write_file(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_existing(path: Path, expected: dict[str, bytes]) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BenchmarkCampaignError("existing campaign path is unsafe")
    actual = sorted(item.name for item in path.iterdir() if item.is_file())
    if actual != sorted(expected) or any(
        item.is_symlink() or not item.is_file() for item in path.iterdir()
    ):
        raise BenchmarkCampaignError("existing campaign file set changed")
    for name, content in expected.items():
        if (path / name).read_bytes() != content:
            raise BenchmarkCampaignError(f"existing campaign payload changed: {name}")
