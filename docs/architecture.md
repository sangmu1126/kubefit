# Architecture

KubeFit uses a ports-and-adapters boundary around a deterministic recommendation
domain. Collection and Git hosting are replaceable infrastructure concerns.

```text
Kubernetes API ----\
                    collector -> recommender -> evaluator -> GitOps patch/PR
Prometheus API ----/                    |
                                         +-> FastAPI -> dashboard
```

The recommendation path is read-only. Writing occurs only in a configured Git
repository, and the initial pull request is a draft. Cluster rollout remains the
responsibility of the repository's existing GitOps controller and human approval
policy.

## Recommendation policy v0

- CPU request: observed P95 plus 25% margin, rounded up to 10 millicores
- Memory request: observed P99 plus 25% margin, rounded up to 16 MiB
- CPU limit: 2x recommended request
- Memory limit: 1.5x recommended request
- Enforce small non-zero floors for idle or incomplete observations

These defaults are deliberately transparent and deterministic. Before production
use, the engine will also gate recommendations on observation coverage, workload
restarts, throttling, OOM events, latency, and traffic representativeness.

The initial `estimated_request_reduction_percent` is a capacity-reduction signal,
not a currency estimate. Monetary savings will be produced by the evaluator from
explicit CPU and memory prices so unlike units are never presented as real cost.
