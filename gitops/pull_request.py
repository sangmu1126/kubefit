from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from gitops.bundle import LoadedProposalBundle, load_proposal_bundle
from gitops.manifest import ManifestTarget, ResourceChange

if TYPE_CHECKING:
    from benchmarks import LoadedBenchmarkResult, LoadedCounterbalancedPair


class PullRequestPlanError(RuntimeError):
    """Raised when verified artifacts cannot authorize one pull request plan."""


class RepositoryFileChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    expected_before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_content: str
    after_content: str


class PullRequestPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[2] = 2
    draft: Literal[True] = True
    branch_name: str = Field(pattern=r"^kubefit/[a-z0-9][a-z0-9._-]*$")
    commit_message: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    benchmark_id: str = Field(pattern=r"^benchmark-[0-9a-f]{32}$")
    benchmark_pair_id: str = Field(pattern=r"^benchmark-pair-[0-9a-f]{32}$")
    benchmark_ids: list[str] = Field(min_length=2, max_length=2)
    target: ManifestTarget
    resource_changes: list[ResourceChange] = Field(min_length=1)
    file_change: RepositoryFileChange


def build_pull_request_plan(
    proposal_path: Path,
    benchmark_path: Path,
    benchmark_pair_path: Path,
) -> PullRequestPlan:
    from benchmarks import load_benchmark_result, load_counterbalanced_pair

    proposal = load_proposal_bundle(proposal_path)
    benchmark = load_benchmark_result(benchmark_path)
    benchmark_pair = load_counterbalanced_pair(benchmark_pair_path)
    _validate_artifacts(proposal, benchmark, benchmark_pair)

    before_content = proposal.before_source_manifest.read_text()
    after_content = proposal.after_source_manifest.read_text()
    slug = _branch_slug(proposal.target.namespace, proposal.target.deployment)
    proposal_suffix = proposal.artifact_id.removeprefix("proposal-")[:8]
    return PullRequestPlan(
        branch_name=f"kubefit/{slug}-{proposal_suffix}",
        commit_message=(
            "kubefit: optimize "
            f"{proposal.target.namespace}/{proposal.target.deployment} resources"
        ),
        title=(
            "KubeFit: optimize "
            f"{proposal.target.namespace}/{proposal.target.deployment} resources"
        ),
        body=_render_pull_request_body(proposal, benchmark, benchmark_pair),
        proposal_id=proposal.artifact_id,
        benchmark_id=benchmark.artifact_id,
        benchmark_pair_id=benchmark_pair.artifact_id,
        benchmark_ids=sorted(
            [
                benchmark_pair.before_after.artifact_id,
                benchmark_pair.after_before.artifact_id,
            ]
        ),
        target=proposal.target,
        resource_changes=proposal.patch_report.changes,
        file_change=RepositoryFileChange(
            path=proposal.source_path,
            expected_before_sha256=hashlib.sha256(before_content.encode()).hexdigest(),
            before_content=before_content,
            after_content=after_content,
        ),
    )


def _validate_artifacts(
    proposal: LoadedProposalBundle,
    benchmark: LoadedBenchmarkResult,
    benchmark_pair: LoadedCounterbalancedPair,
) -> None:
    if proposal.workload_uid is None or proposal.workload_created_at is None:
        raise PullRequestPlanError("proposal does not contain workload identity evidence")
    if benchmark.proposal_id != proposal.artifact_id:
        raise PullRequestPlanError("benchmark result does not reference the proposal")
    if benchmark_pair.proposal_id != proposal.artifact_id:
        raise PullRequestPlanError("counterbalanced pair does not reference the proposal")
    if benchmark.artifact_id != benchmark_pair.before_after.artifact_id:
        raise PullRequestPlanError(
            "primary benchmark must be the before-after member of the counterbalanced pair"
        )
    if benchmark.verdict.status != "pass":
        raise PullRequestPlanError(
            f"benchmark verdict must be pass, got {benchmark.verdict.status}"
        )
    if benchmark.before.request_cost_usd != proposal.before_request_cost_usd:
        raise PullRequestPlanError("baseline benchmark cost conflicts with proposal")
    if benchmark.after.request_cost_usd != proposal.after_request_cost_usd:
        raise PullRequestPlanError("candidate benchmark cost conflicts with proposal")


def _branch_slug(namespace: str, deployment: str) -> str:
    raw = f"{namespace}-{deployment}".lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    if not slug:
        raise PullRequestPlanError("target cannot produce a safe branch name")
    return slug


def _render_pull_request_body(
    proposal: LoadedProposalBundle,
    benchmark: LoadedBenchmarkResult,
    benchmark_pair: LoadedCounterbalancedPair,
) -> str:
    evaluation = proposal.evaluation
    before = benchmark.before
    after = benchmark.after
    lines = [
        "## Why",
        "",
        (
            "KubeFit observed this workload and proposes a reviewed resource change "
            "after a reproducible before/after benchmark passed its safety policy."
        ),
        "",
        f"- Target: `{proposal.target.namespace}/{proposal.target.deployment}` "
        f"container `{proposal.target.container}`",
        f"- Workload UID: `{proposal.workload_uid}`",
        f"- Proposal: `{proposal.artifact_id}`",
        f"- Benchmark: `{benchmark.artifact_id}`",
        f"- Counterbalanced pair: `{benchmark_pair.artifact_id}`",
        "- Pair benchmarks: "
        + ", ".join(
            f"`{benchmark_id}`"
            for benchmark_id in sorted(
                trial.benchmark_id for trial in benchmark_pair.assessment.trials
            )
        ),
        f"- Benchmark verdict: **{benchmark.verdict.status.upper()}**",
        f"- Pair verdict: **{benchmark_pair.assessment.status.upper()}**",
        "",
        "## Proposed resources",
        "",
        "| Field | Current | Recommended |",
        "|---|---:|---:|",
    ]
    lines.extend(
        f"| `{change.field}` | `{_markdown_cell(change.current)}` | "
        f"`{_markdown_cell(change.recommended)}` |"
        for change in proposal.patch_report.changes
    )
    lines.extend(
        [
            "",
            "## Cost projection",
            "",
            "| Basis | Current | Recommended | Change |",
            "|---|---:|---:|---:|",
            (
                "| Monthly resource requests (USD) | "
                f"{evaluation.cost.current.total_usd} | "
                f"{evaluation.cost.recommended.total_usd} | "
                f"{benchmark.verdict.cost_change_percent}% |"
            ),
            "",
            f"Price source: `{_markdown_cell(evaluation.cost.assumptions.price_source)}`. "
            "This is a request-cost projection, not a guaranteed invoice reduction.",
            "",
            "## Benchmark evidence",
            "",
            "| Signal | Before | After |",
            "|---|---:|---:|",
            _row(
                "Steady latency P95 (ms)",
                before.steady.latency_p95_ms,
                after.steady.latency_p95_ms,
            ),
            _row(
                "Steady latency P99 (ms)",
                before.steady.latency_p99_ms,
                after.steady.latency_p99_ms,
            ),
            _row("Spike latency P95 (ms)", before.spike.latency_p95_ms, after.spike.latency_p95_ms),
            _row("Spike latency P99 (ms)", before.spike.latency_p99_ms, after.spike.latency_p99_ms),
            _row(
                "CPU throttling P95 (%)",
                before.runtime.cpu_throttling_p95_percent,
                after.runtime.cpu_throttling_p95_percent,
            ),
            _row("OOMKilled", before.runtime.oom_killed_count, after.runtime.oom_killed_count),
            _row("Restarts", before.runtime.restart_count, after.runtime.restart_count),
            _row(
                "Spike recovery (s)",
                before.runtime.traffic_spike_recovery_seconds,
                after.runtime.traffic_spike_recovery_seconds,
            ),
            "",
            "## Review notes",
            "",
        ]
    )
    benchmark_warnings = [
        warning
        for warning in benchmark.verdict.warnings
        if "one sequential trial cannot" not in warning
    ]
    notes = (
        proposal.patch_report.eligibility_warnings
        + benchmark_warnings
        + benchmark_pair.assessment.warnings
    )
    if notes:
        lines.extend(f"- {_markdown_cell(note)}" for note in notes)
    else:
        lines.append("- No policy warnings were emitted.")
    lines.extend(
        [
            "",
            "## Rollback",
            "",
            (
                f"Revert the manifest commit for `{proposal.source_path}` and let the "
                "existing GitOps controller reconcile the previous requests and limits."
            ),
            "Do not merge automatically; this pull request must remain draft until human review.",
            "",
            "---",
            "Generated by KubeFit from content-addressed proposal and benchmark artifacts.",
        ]
    )
    return "\n".join(lines) + "\n"


def _row(label: str, before: object, after: object) -> str:
    return f"| {label} | {before} | {after} |"


def _markdown_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")
