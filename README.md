# KubeFit

KubeFit is a GitOps-first Kubernetes resource optimization platform. It analyzes
real workload metrics, explains its recommendations, and proposes reviewed YAML
changes through GitHub pull requests instead of mutating production workloads.

> Measure first. Explain the trade-off. Change through GitOps.

## MVP scope

- Analyze Kubernetes `Deployment` resources
- Read CPU and memory usage from Prometheus
- Recommend requests and limits from CPU P95 and memory P99
- Compare estimated cost and operational risk
- Generate a Kubernetes YAML patch
- Open a GitHub draft pull request
- Compare before/after behavior with load tests

HPA recommendations, multi-cloud support, predictive incident detection,
Terraform generation, and an AI chatbot are intentionally outside the first
release.

## Repository layout

```text
collector/       Kubernetes and Prometheus adapters
recommender/     Resource recommendation domain logic
evaluator/       Cost, stability, and performance evaluation
gitops/          YAML patch and GitHub pull request integration
api/             FastAPI application
dashboard/       React dashboard (after the core workflow)
deploy/          Helm chart and demo manifests
benchmarks/      Load tests and reproducible comparisons
docs/            Architecture, security, and evaluation records
tests/           Unit and integration tests
```

The ordered delivery phases and their completion criteria are documented in
[`docs/implementation-plan.md`](docs/implementation-plan.md).

To run KubeFit against a disposable kind cluster with Prometheus, follow the
[`docs/local-development.md`](docs/local-development.md) guide.

Engineering decisions, failed assumptions, diagrams, and reproducible evidence are
recorded in the [`docs/devlog/`](docs/devlog/README.md) development journal.

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
uvicorn api.main:app --reload
```

Then open `http://localhost:8000/docs`. Use `POST /v1/recommendations` for the
capacity result alone or `POST /v1/evaluations` for a recommendation plus an
explicit request-cost comparison.

Analyze a live Deployment (with Prometheus reachable locally):

```bash
kubefit analyze --namespace kubefit-demo --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --cpu-core-hour-usd 0.04 \
  --memory-gib-hour-usd 0.005 \
  --price-source example://local-model
```

The CLI invokes `kubectl` using the current context, reads only Deployment and
Pod metadata, queries Prometheus, and prints a recommendation with its evidence.
Prometheus must scrape cAdvisor metrics plus `kube_pod_owner` from
kube-state-metrics. KubeFit filters ReplicaSets through their Kubernetes controller
owner UID and clips history to the current Deployment creation time, avoiding
Pod-name prefixes and same-name workload history.

The evaluator also reads the cAdvisor CPU throttled-period counters and current
Kubernetes target-container status. Missing throttling metrics or incomplete Pod
status coverage keeps the relevant risk `unknown`; an observed OOMKilled state is
reported as high risk even when the wider observation window is incomplete.

Every evaluation includes `patch_eligibility`. Future GitOps patch generation must
require `eligible`; insufficient readiness and high or unknown safety risks produce
stable machine-readable blocking checks. Medium risk remains an explicit reviewer
warning and never becomes an invisible pass.

The `gitops` package can generate a stale-safe manifest proposal from an eligible
evaluation. It matches one `apps/v1` Deployment and container across supplied YAML
sources, verifies that repository resources still equal the evaluated workload, and
returns patched content, a unified diff, and a SHA-256-backed report without writing
the file. Only the changed resource scalar ranges are replaced, preserving unrelated
documents, fields, comments, order, and quoting.

An eligible patch can be published as an immutable proposal bundle containing the
before/after manifests, canonical evaluation, diff, report, and benchmark context.
Its ID is derived from content, every payload is indexed by SHA-256 and byte size,
and publication uses a private staging directory followed by one directory rename.
Identical retries reuse the bundle; modified or partial existing content is rejected.

The `benchmarks` package defines `kubefit-load-v1`, a fixed k6 warmup → steady →
spike → recovery profile, plus typed before/after measurements and an explicit
safety verdict. Results must reference the same proposal, profile, and complete
offered load before latency, errors, throttling, OOM, and recovery regressions are
evaluated. Request-cost change is reported separately and cannot override safety.

The benchmark execution core revalidates every proposal hash before cluster access,
requires an explicit kubectl context, executes before and after sequentially, and
reapplies the before manifest on every exit path after mutation starts. It is
currently an internal building block for the disposable demo cluster, not a
production automation interface; measurement collection and isolated result
publication are still in progress.

The aligned measurement collector brackets the fixed k6 run with Pod-level runtime
snapshots, queries Prometheus throttling inside that run, derives recovery from raw
timestamped samples, and selects the proposal-fixed monthly request cost. Its output
records run timestamps, Pod identity, and raw/summary hashes. Raw evidence remains
temporary until the result-artifact phase is completed, so the end-to-end benchmark
is not yet exposed as a supported CLI command.

Completed benchmark executions can now be published separately from their proposal
as immutable `benchmark-<digest>` artifacts. Each result binds canonical before/after
measurements, exact k6 summaries and raw streams, the recomputed verdict, and a
human-readable report with per-file hashes. Identical publication retries reuse the
same result; tampered or partial existing content is never overwritten.

`kubefit benchmark` composes the local workflow under a Deployment-scoped OS lock.
It requires an explicit `kind-*` context and `--confirm-disposable-cluster`, holds
the lock through restoration and result publication, then prints a compact JSON
handoff. Port-forwards and an existing immutable proposal are currently required;
see [`docs/local-development.md`](docs/local-development.md).

`kubefit analyze` emits a typed artifact binding evaluation evidence to Deployment
UID and creation time. `kubefit propose` consumes that identity directly with
repository-bounded YAML sources and publishes an immutable proposal; it does not
allow the target to be retyped. Benchmark preflight rejects a recreated Deployment
before any apply. Multi-document source files remain byte-exact review evidence,
while benchmark apply and restoration use separately hashed, single-Deployment
manifests so neighboring Services or workloads are never reconciled.

The example prices are illustrative, not a cloud-provider price claim. Live
analysis requires the caller to provide CPU and memory rates plus a source label.
The output repeats those assumptions and separates current/recommended CPU and
memory request costs. Projected request savings do not necessarily become invoice
savings because node fragmentation, discounts, taxes, and autoscaling replica-hours
are outside this model.

The optional identity store retains observed ReplicaSet names after Kubernetes
deletes their objects. It is an atomic local JSON snapshot containing identifiers
only; a new Deployment UID replaces same-name history rather than merging it.

Example request:

```json
{
  "current": {
    "cpu_request_millicores": 1000,
    "cpu_limit_millicores": 2000,
    "memory_request_mib": 2048,
    "memory_limit_mib": 4096
  },
  "observed": {
    "cpu_p95_millicores": 230,
    "memory_p99_mib": 710,
    "cpu_max_millicores": 400,
    "memory_max_mib": 900,
    "sample_count": 1900,
    "observation_coverage": 0.95,
    "desired_replicas": 2,
    "available_replicas": 2,
    "observed_replicas": 2
  }
}
```

For `/v1/evaluations`, add the following top-level fields to the same request:

```json
{
  "cost_assumptions": {
    "cpu_core_hour_usd": "0.04",
    "memory_gib_hour_usd": "0.005",
    "monthly_hours": "730",
    "price_source": "example://local-model"
  },
  "replica_count": 2
}
```

When maxima or sufficient observation coverage are unavailable, KubeFit reports
the related risk as `unknown` rather than presenting an unsupported low-risk claim.
The recommendation is actionable only when its `readiness.status` is `ready`.

## Project origin

KubeFit grew from lessons about over-allocation and observability learned while
operating an earlier serverless platform. It is independently designed and
implemented as an open-source Kubernetes optimization tool. The prior project
is context, not this repository's codebase or deployment architecture.

## Safety principles

- Read workloads and metrics; never mutate a cluster in the recommendation path.
- Submit changes as draft pull requests with evidence and rollback guidance.
- Never store Kubernetes, Prometheus, or GitHub credentials in the repository.
- Keep recommendation policy deterministic and independently testable.
