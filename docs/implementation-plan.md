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
machine-readable. Entry [0035](devlog/0035-live-k6-evidence-boundary.md) runs the
unchanged baseline against the real Service, fixes P99 summary export, and aligns
the offered-load contract with k6's observed boundary scheduling. Entry
[0036](devlog/0036-k6-process-success-boundary.md) rejects k6's structured script
exception even when the process exits zero, while preserving mandatory typed-output
checks. A real eligible before/after disposable-cluster run remains open while the
volume collects enough continuous evidence. Entry
[0037](devlog/0037-controlled-demo-observation.md) replaces the meaningless local
24-hour idle wait with a fixed one-hour, loaded, explicitly non-production demo
profile while preserving the multi-day production default. The first controlled
run is in progress; its result is not yet claimed.

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
Entry [0024](devlog/0024-publish-cli-secret-boundary.md) exposes the complete command
with environment-only token intake, explicit mutation acknowledgement, and safe JSON
output. Entry [0025](devlog/0025-live-publication-preflight.md) adds mutation-free
artifact, local Git, remote ref, and API readiness diagnostics. Authentication repair,
a disposable target, and the live two-run demonstration remain open. Entry
[0026](devlog/0026-live-demo-contract.md) makes blocked readiness fail closed for
automation and defines the exact disposable setup, two-run assertions, independent
GitHub evidence, and archive-first cleanup procedure. Entry
[0027](devlog/0027-publication-evidence-verifier.md) binds that exact five-file proof
back to the immutable proposal/benchmark and emits a content-addressed verification
result. The authenticated live run itself remains open.

**Goal:** deliver the validated change through the repository's review workflow.

- Create a dedicated branch and commit only the manifest patch and report.
- Open a draft pull request with evidence, expected cost change, risks, benchmark
  results, and rollback guidance.
- Make repeated runs idempotent and never commit credentials.
- Leave merge and cluster rollout to humans and the existing GitOps controller.

**Done when:** the demo produces a draft PR whose claims can be traced back to
collected metrics and benchmark artifacts.

## Phase 6: presentation layer and packaging

**Status: in progress.** Entry
[0028](devlog/0028-least-privilege-helm-package.md) adds a non-root API image and
Helm chart with tokenless defaults, explicit resources/probes, and namespace-scoped
opt-in read-only RBAC. Entry [0029](devlog/0029-kind-helm-integration.md) completes the
local image build, kind installation, live health probe, RBAC allow/deny matrix, and
tokenless restoration evidence. Entry
[0030](devlog/0030-explainable-review-dashboard.md) adds an API-backed review surface
for recommendation evidence, cost, risk, and the patch gate without duplicating
analysis logic. Entry [0031](devlog/0031-packaged-dashboard.md) builds that UI in a
separate Node stage, serves it from the hardened API image, and verifies it through
the kind Helm Service without expanding RBAC. Entry
[0032](devlog/0032-verified-image-sbom.md) binds a tamper-evident SPDX inventory to
the exact local image ID. Entry
[0033](devlog/0033-analysis-artifact-review.md) connects CLI analysis output to the
dashboard through an API-owned integrity review while exposing schema v1 replay
limits. Entry [0034](devlog/0034-replayable-analysis-schema.md) adds schema v2
aggregate observations and versioned policy inputs, replays the complete decision,
and preserves v1 content identities. Raw percentile replay, vulnerability policy,
source provenance, signing, and external image/chart publication remain open.

**Goal:** make the completed workflow easy to install and demonstrate.

- Add a small dashboard over the existing API after the CLI workflow is complete.
  Example review, v1 integrity review, v2 recommendation replay, and packaged
  delivery are complete. Raw percentile replay remains explicitly out of scope.
- Package KubeFit with a Helm chart and least-privilege read-only RBAC.
- Document security boundaries, metric prerequisites, limitations, and rollback.

HPA recommendations, multi-cloud pricing catalogs, predictive incident detection,
Terraform generation, and an AI chatbot remain outside the first release.
