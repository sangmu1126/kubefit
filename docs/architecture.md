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

## Historical workload identity

Container usage is joined with `kube_pod_owner` and `kube_replicaset_owner` at each
Prometheus range-query timestamp. This associates old and current ReplicaSets with
the exact Deployment name while preserving a separate series per Pod. It avoids the
false matches possible with Deployment-name prefixes and does not require deleted
Pods to remain in the live Kubernetes API.

kube-state-metrics ownership retention is therefore an explicit metric prerequisite.
A same-name Deployment deletion and recreation cannot yet be distinguished without
additional UID evidence.

## Recommendation policy v0

- CPU request: observed P95 plus 25% margin, rounded up to 10 millicores
- Memory request: observed P99 plus 25% margin, rounded up to 16 MiB
- CPU limit: 2x recommended request
- Memory limit: 1.5x recommended request
- Enforce small non-zero floors for idle or incomplete observations
- Calculate each percentile per Pod and retain the busiest Pod's value
- Require 70% observation coverage and at least 100 metric samples
- Require desired, available, and observed replica counts to match

These defaults are deliberately transparent and deterministic. Before production
use, the engine will also gate recommendations on observation coverage, workload
restarts, throttling, OOM events, latency, and traffic representativeness.

CPU and memory request changes are reported separately because millicores and MiB
cannot be combined into a meaningful percentage. Monetary savings will be produced
by the evaluator from explicit CPU and memory prices, replica count, and time.

Resource calculation and change authorization are separate. An insufficient result
still includes its candidate and evidence for inspection, but future patch generation
must accept only a recommendation whose readiness status is `ready`.
