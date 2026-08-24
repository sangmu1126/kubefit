# Development journal

This journal records how KubeFit evolves, not only what the latest code does.
Each entry connects a product claim to a decision, implementation, and reproducible
piece of evidence.

## What to record

Every meaningful development slice answers the same questions:

1. **Why:** What user or operational risk made this work necessary?
2. **What:** What behavior, boundary, or artifact changed?
3. **How:** How was it implemented, including alternatives and trade-offs?
4. **Evidence:** Which test, command, metric, or benchmark supports the result?
5. **Decision:** What is safe to claim now, and what remains unknown?
6. **Next:** Which uncertainty should the next slice reduce?

Failures and rejected approaches belong in the journal. They are often stronger
evidence of engineering judgment than a final screenshot without context.

## Entry index

| Entry | Focus | Main outcome |
|---|---|---|
| [0001](0001-trustworthy-analysis.md) | Trustworthy analysis foundation | Correct per-Pod metrics and a real kind/Prometheus validation path |
| [0002](0002-observation-readiness.md) | Observation readiness | Block actionable changes when metric or replica evidence is incomplete |
| [0003](0003-rollout-history.md) | Rollout history | Preserve previous ReplicaSet metrics while excluding similar names |
| [0004](0004-recreation-boundary.md) | Recreation boundary | Isolate same-name workloads with owner UID and creation-time clipping |
| [0005](0005-identity-snapshot.md) | Identity snapshot | Recover observed ReplicaSet history after API deletion |
| [0006](0006-resource-input-boundary.md) | Resource input boundary | Close Phase 1 with exact quantities and complete selectors |
| [0007](0007-explicit-cost-model.md) | Explicit cost model | Separate transparent request-cost projections from readiness |
| [0008](0008-runtime-safety-signals.md) | Runtime safety signals | Ground risk in throttling, restarts, OOM, and signal coverage |
| [0009](0009-patch-eligibility-gate.md) | Patch eligibility | Close Phase 2 with one structured GitOps safety gate |
| [0010](0010-minimal-manifest-patch.md) | Minimal manifest patch | Generate a stale-safe four-scalar diff without reformatting YAML |
| [0011](0011-reproducible-proposal-bundle.md) | Proposal bundle | Publish immutable, content-addressed benchmark inputs atomically |
| [0012](0012-fixed-load-profile.md) | Fixed load profile | Version comparable before/after load and produce explicit safety verdicts |
| [0013](0013-restoring-benchmark-runner.md) | Restoring benchmark runner | Verify inputs, execute sequentially, and restore before returning |
| [0014](0014-aligned-benchmark-measurement.md) | Aligned benchmark measurement | Align k6, Prometheus, Kubernetes deltas, and proposal-fixed cost |
| [0015](0015-immutable-benchmark-result.md) | Immutable benchmark result | Retain raw evidence and publish a content-addressed, retry-safe result |
| [0016](0016-locked-benchmark-cli.md) | Locked benchmark CLI | Serialize Deployment mutation and compose the full local workflow |
| [0017](0017-proposal-cli.md) | Proposal CLI | Bind analysis identity and repository YAML into an immutable proposal |
| [0018](0018-target-document-isolation.md) | Target document isolation | Apply only the selected Deployment, never neighboring YAML documents |
| [0019](0019-persistent-local-observation.md) | Persistent local observation | Preserve Prometheus evidence across Pod and Docker restarts |
| [0020](0020-readiness-cli.md) | Readiness CLI | Explain whether to wait or intervene before creating a proposal |
| [0021](0021-pull-request-plan.md) | Pull request plan | Turn verified proposal and benchmark evidence into one review contract |
| [0022](0022-transactional-git-commit.md) | Transactional Git commit | Commit one verified file without leaving the checkout on the generated branch |
| [0023](0023-idempotent-draft-pull-request.md) | Idempotent draft pull request | Publish one verified commit without overwriting a remote branch or duplicate PR |
| [0024](0024-publish-cli-secret-boundary.md) | Publish CLI secret boundary | Compose verified publication without accepting or printing a token value |
| [0025](0025-live-publication-preflight.md) | Live publication preflight | Explain artifact, Git, remote, and credential blockers without mutation |
| [0026](0026-live-demo-contract.md) | Live demo contract | Make readiness machine-enforceable and define disposable two-run evidence |
| [0027](0027-publication-evidence-verifier.md) | Publication evidence verifier | Bind two-run GitHub proof back to immutable proposal and benchmark artifacts |
| [0028](0028-least-privilege-helm-package.md) | Least-privilege Helm package | Package the API non-root and scope optional observation RBAC by namespace |
| [0029](0029-kind-helm-integration.md) | kind Helm integration | Build, install, probe, verify RBAC denials, and restore tokenless defaults |
| [0030](0030-explainable-review-dashboard.md) | Explainable review dashboard | Visualize API-owned recommendation, cost, risk, and GitOps gating without duplicating analysis |
| [0031](0031-packaged-dashboard.md) | Packaged dashboard | Build and serve the immutable review UI from the hardened API image |
| [0032](0032-verified-image-sbom.md) | Verified image SBOM | Bind an immutable SPDX inventory to the exact local image ID and detect tampering |
| [0033](0033-analysis-artifact-review.md) | Analysis artifact review | Validate and visualize CLI artifacts while exposing schema v1 replay limits |
| [0034](0034-replayable-analysis-schema.md) | Replayable analysis schema | Replay v2 recommendations while preserving v1 identities and observation gates |
| [0035](0035-live-k6-evidence-boundary.md) | Live k6 evidence boundary | Export P99 and accept only matching boundary overshoot in real fixed-load runs |
| [0036](0036-k6-process-success-boundary.md) | k6 process success boundary | Reject script exceptions even when k6 returns exit code zero |
| [0037](0037-controlled-demo-observation.md) | Controlled demo observation | Separate real multi-day evidence from a fixed one-hour loaded demo window |
| [0038](0038-live-demo-benchmark.md) | Live demo benchmark | Turn three invalid harness runs into a restored, passing before/after result |
| [0039](0039-authenticated-publication-preflight.md) | Authenticated publication preflight | Prove the PASS artifacts and GitHub read boundary are ready without mutation |
| [0040](0040-live-origin-draft-pr.md) | Live origin Draft PR | Create once, reuse once, and independently verify the real one-file GitOps handoff |
| [0041](0041-repository-quality-gates.md) | Repository quality gates | Make Python, dashboard, Helm, and Docker failures visible as independent CI checks |
| [0042](0042-index-bound-benchmark-review.md) | Index-bound benchmark review | Replay indexed before/after evidence on the API and visualize its decision boundary |
| [0043](0043-shareable-full-benchmark-review.md) | Shareable full benchmark review | Open a configured result by artifact-ID URL after complete server-side verification |
| [0044](0044-packaged-runtime-ci-gate.md) | Packaged runtime CI gate | Start the built image and verify health, dashboard, defaults, logs, and cleanup |

## Visual language

Use the smallest visual that explains the decision:

- Mermaid flowchart for data flow and component boundaries
- Mermaid sequence diagram for runtime or GitOps interactions
- Table for before/after metrics and alternatives
- Screenshot only when UI state is itself the evidence

Every visual must have a sentence explaining the conclusion. Raw diagrams without
an engineering decision are not useful evidence.

## Writing workflow

1. Copy [`_template.md`](_template.md) when a development slice starts.
2. Write the problem and success criteria before implementation.
3. Add failed observations while working; do not reconstruct them from memory.
4. Record exact commands and quantitative results after verification.
5. Link the commits only after the slice has been committed.
6. End with the next unresolved question, not a generic task list.

Use sequential filenames such as `0002-cost-model.md`. One entry may cover several
small commits when they answer the same engineering question.
