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
