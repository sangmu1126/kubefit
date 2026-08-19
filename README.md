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

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
pytest
uvicorn api.main:app --reload
```

Then open `http://localhost:8000/docs` and call `POST /v1/recommendations`.

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
    "memory_p99_mib": 710
  }
}
```

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

