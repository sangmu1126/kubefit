# Implementation plan

KubeFit is built as a sequence of verifiable vertical slices. Each phase must
produce evidence that can be demonstrated independently before the next phase
adds more automation.

## Guiding constraints

- A recommendation is calculated per container replica, because Kubernetes
  resource requests and limits are applied to each Pod.
- The recommendation path never mutates a Kubernetes cluster.
- Cost reduction and operational risk are reported separately.
- Insufficient or unrepresentative observations must be visible to the user.
- Every Git change starts as a reviewable draft pull request.

## Development evidence

Each meaningful implementation slice must add or update an entry in the
[`devlog`](devlog/README.md). Entries record why the work mattered, how the decision
was implemented, which alternatives were rejected, and what reproducible evidence
supports the resulting claim. Failures and remaining uncertainty are documented at
the time they are observed.

## Phase 1: trustworthy resource analysis

**Status: complete for the MVP (2026-08-21).** Validation evidence is recorded in
development journal entries [0001](devlog/0001-trustworthy-analysis.md) through
[0006](devlog/0006-resource-input-boundary.md).

**Goal:** produce a deterministic recommendation from correctly scoped metrics.

- Read a Deployment, its target container, replicas, resources, and matching Pods.
- Query CPU and memory as per-Pod time series rather than a Deployment-wide sum.
- Authorize ReplicaSets through the current Deployment owner UID.
- Clip same-name history at the current Deployment creation timestamp.
- Optionally retain observed ReplicaSet identity after API deletion.
- Parse Kubernetes quantities exactly and support `matchLabels` plus
  `matchExpressions` selectors.
- Use the highest per-Pod CPU P95 and memory P99 so one busy replica is not hidden
  by an average.
- Report sample count and observation coverage.
- Gate actionable recommendations on metric coverage, sample count, and stable replicas.
- Recommend CPU and memory requests with explicit margins and rounding rules.
- Report CPU and memory request changes separately; never add millicores and MiB.

**Done when:** unit tests cover multiple replicas, missing samples, unit conversion,
rounding, and both upsize and downsize recommendations.

## Phase 2: cost and safety evaluation

**Status: complete for the MVP (2026-08-21).** Explicit cost assumptions, runtime
safety signals, and the patch eligibility gate are recorded in development journal
entries [0007](devlog/0007-explicit-cost-model.md) through
[0009](devlog/0009-patch-eligibility-gate.md).

**Goal:** turn a capacity recommendation into a defensible decision.

- Calculate current and recommended monthly costs from explicit CPU and memory
  unit prices, replica count, and monthly hours.
- Collect CPU throttling, container restarts, OOMKilled events, and usage maxima.
- Gate automatic patch generation when observation coverage is insufficient.
- Classify risks from observed signals, not only from the recommendation formula.
- Include all assumptions and warning reasons in the result.

**Done when:** a result explains its price assumptions, projected savings, confidence,
and the signals that produced each risk classification.

## Phase 3: manifest patch generation

**Status: complete for the MVP (2026-08-21).** Minimal scalar replacement, stale
input protection, ambiguity rejection, and golden evidence are recorded in
development journal entry [0010](devlog/0010-minimal-manifest-patch.md).

**Goal:** create a minimal, reviewable GitOps change without touching the cluster.

- Locate the selected Deployment and container in a repository manifest.
- Preserve unrelated YAML fields and formatting where practical.
- Change only CPU and memory requests/limits selected by the policy.
- Produce a unified diff and structured recommendation report.
- Refuse ambiguous workload or container matches.

**Done when:** golden-file tests prove that only the intended container resources
change and invalid or ambiguous manifests fail safely.

## Phase 4: reproducible before/after benchmark

**Status: in progress.** Immutable proposal inputs and the fixed load/verdict
contract were completed on 2026-08-21 and are documented in development journal
entries [0011](devlog/0011-reproducible-proposal-bundle.md),
[0012](devlog/0012-fixed-load-profile.md), and
[0013](devlog/0013-restoring-benchmark-runner.md). The restoring execution core is
also complete. Entry [0014](devlog/0014-aligned-benchmark-measurement.md) adds the
time-aligned k6, Prometheus, Kubernetes-delta, and proposal-cost collector.
Entry [0015](devlog/0015-immutable-benchmark-result.md) completes durable raw
evidence and atomic result publication. Entry
[0016](devlog/0016-locked-benchmark-cli.md) completes the Deployment-scoped execution
lock and local CLI composition. Entry
[0017](devlog/0017-proposal-cli.md) completes the analysis-bound proposal command and
live Deployment identity preflight. Entry
[0018](devlog/0018-target-document-isolation.md) separates full review provenance
from single-Deployment executable manifests. Entry
[0019](devlog/0019-persistent-local-observation.md) preserves local Prometheus
history across Pod recreation so readiness can accumulate honestly. Entry
[0020](devlog/0020-readiness-cli.md) makes that progress and its time assumptions
machine-readable. A real eligible disposable-cluster run remains open while the new
volume collects enough evidence.

**Goal:** validate savings without concealing performance regressions.

- Provide a local demo cluster setup and an intentionally overprovisioned service.
- Run a fixed load profile before and after the proposed patch.
- Compare request cost, latency P95/P99, throttling, OOM events, error rate, and
  recovery time after a traffic spike.
- Store machine-readable benchmark results and a human-readable summary.

**Done when:** another developer can reproduce the comparison from documented
commands and obtain a pass/fail safety verdict.

## Phase 5: GitHub draft pull request

**Status: in progress.** Entry [0021](devlog/0021-pull-request-plan.md) establishes
load-time semantic verification for proposal/result artifacts and produces a
deterministic, draft-only, one-file review contract. Entry
[0022](devlog/0022-transactional-git-commit.md) adds stale-safe, one-file local branch
commits with rollback and idempotent reuse. Entry
[0023](devlog/0023-idempotent-draft-pull-request.md) adds compare-and-swap branch
publication and exact-contract Draft PR creation with ambiguous-response recovery.
CLI credential intake and a live disposable-repository demonstration remain open.

**Goal:** deliver the validated change through the repository's review workflow.

- Create a dedicated branch and commit only the manifest patch and report.
- Open a draft pull request with evidence, expected cost change, risks, benchmark
  results, and rollback guidance.
- Make repeated runs idempotent and never commit credentials.
- Leave merge and cluster rollout to humans and the existing GitOps controller.

**Done when:** the demo produces a draft PR whose claims can be traced back to
collected metrics and benchmark artifacts.

## Phase 6: presentation layer and packaging

**Goal:** make the completed workflow easy to install and demonstrate.

- Add a small dashboard over the existing API after the CLI workflow is complete.
- Package KubeFit with a Helm chart and least-privilege read-only RBAC.
- Document security boundaries, metric prerequisites, limitations, and rollback.

HPA recommendations, multi-cloud pricing catalogs, predictive incident detection,
Terraform generation, and an AI chatbot remain outside the first release.
