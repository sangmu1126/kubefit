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
