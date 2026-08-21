# Local Kubernetes and Prometheus

The local environment runs a single-node kind cluster in Docker and installs a
minimal kube-prometheus-stack. Prometheus scrapes the kubelet/cAdvisor metrics
used by KubeFit for container CPU and memory analysis.

## Prerequisites

- Docker Desktop running
- `kubectl`
- `kind`
- `helm`
- the Python development environment from the project quick start

On macOS, the missing command-line tools can be installed with:

```bash
brew install kind helm
```

## Create the environment

```bash
./deploy/local/up.sh
```

The script is idempotent. It creates the `kubefit` kind cluster, installs pinned
version `88.5.0` of `kube-prometheus-stack`, and applies the demo Deployment.
Grafana, Alertmanager, and control-plane monitoring are disabled to keep local
resource usage small. Prometheus stores its two-day history on a 5 GiB PVC with a
4 GB retention-size ceiling, so Pod and Docker restarts do not reset observation
coverage.

Upgrading a cluster created before this storage configuration replaces the old
emptyDir-backed Prometheus Pod once. Its existing metrics are not migrated, so the
readiness accumulation window begins again after that first upgrade.

Check the resulting workloads:

```bash
kubectl --context kind-kubefit get pods -n monitoring
kubectl --context kind-kubefit get pods -n kubefit-demo
kubectl --context kind-kubefit get pvc -n monitoring
```

## Check observation readiness

Keep this command running in one terminal:

```bash
kubectl --context kind-kubefit port-forward \
  -n monitoring \
  svc/monitoring-kube-prometheus-prometheus \
  9090:9090
```

After Prometheus has collected several samples, run in another terminal:

```bash
kubefit readiness \
  --context kind-kubefit \
  --namespace kubefit-demo \
  --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --days 1
```

`collecting` includes an estimated readiness timestamp only when replicas, Pod
metrics, and container statuses are complete. `blocked` requires intervention and
does not include a time estimate. The estimate assumes the replica count and
five-minute metric production remain stable.

## Analyze the demo Deployment

When readiness becomes `eligible`, create the priced analysis artifact:

```bash
kubefit analyze \
  --context kind-kubefit \
  --namespace kubefit-demo \
  --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --days 1 \
  --cpu-core-hour-usd 0.04 \
  --memory-gib-hour-usd 0.005 \
  --price-source example://local-model \
  > .kubefit/analysis.json
```

These rates are deliberately labeled example inputs. Replace them and the source
label with assumptions appropriate to the environment being evaluated. The default
billing horizon is 730 hours and can be changed with `--monthly-hours`.

A new cluster cannot provide a full one-day observation window, so KubeFit will
correctly report low observation coverage and `unknown` risk until enough samples
have accumulated. A mathematical cost projection is still shown for inspection,
but it does not make an insufficient recommendation actionable.

The analysis artifact also includes CPU throttling P95 and its independent coverage, plus
restart and OOMKilled counts from the current target-container statuses. A quiet new
cluster still reports `unknown` rather than `low` until both usage and throttling
windows satisfy the readiness gates.

## Review an evaluation in the dashboard

The first dashboard slice is a local review surface over the existing evaluation
API. Start the API in one terminal:

```bash
uvicorn api.main:app --reload
```

Install the locked frontend dependencies and start Vite in another terminal:

```bash
npm --prefix dashboard install
npm --prefix dashboard run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/v1` and `/healthz` to
`http://127.0.0.1:8000`, so no permissive CORS policy is required. Choose
`검증 가능` to inspect an eligible example or `근거 부족` to see why a numerical
cost projection remains blocked when coverage and safety signals are absent.

The examples are editable API payloads, not live cluster collection. Price values
use `example://local-model` and must not be presented as provider prices or measured
invoice savings.

Verify the frontend independently with:

```bash
npm --prefix dashboard run build
npm --prefix dashboard test
npm --prefix dashboard audit
```

The two-process setup is for hot-reload development. The Docker build runs the
locked frontend build in a separate Node stage and copies only `dist/` into the
non-root Python runtime. The Helm Service therefore serves the same dashboard at
`/` without Vite or a CORS policy.

## Run a published proposal benchmark

The benchmark command is intentionally restricted to an explicit kind context and
requires acknowledgement because it temporarily applies before and after manifests.
Create an immutable proposal from an eligible analysis without retyping its target:

```bash
kubefit propose \
  --analysis .kubefit/analysis.json \
  --repository-root . \
  --manifest deploy/demo/overprovisioned-api.yaml
```

Then keep separate Service and Prometheus port-forwards running:

```bash
kubectl --context kind-kubefit port-forward \
  -n kubefit-demo service/overprovisioned-api 8080:80

kubectl --context kind-kubefit port-forward \
  -n monitoring service/monitoring-kube-prometheus-prometheus 9090:9090

kubefit benchmark \
  --proposal .kubefit/proposals/proposal-<digest> \
  --target-url http://localhost:8080/ \
  --prometheus-url http://localhost:9090 \
  --context kind-kubefit \
  --confirm-disposable-cluster
```

The command locks the target Deployment, revalidates its analysis identity, restores
the before manifest on every exit path, and publishes a separate immutable result
under `benchmarks/results/`. If the repository YAML contains multiple documents,
only the selected Deployment document is applied; the complete source remains in
the proposal solely as review provenance. This sequence has not yet been claimed as
a completed live benchmark.

## Publish a verified Draft PR

Publication requires a passing immutable benchmark result, a clean repository on an
attached branch, and a credential-free public GitHub remote. Git push authentication
must already be available through an SSH agent or Git credential helper. Supply the
GitHub API token through an environment variable, never as a command argument. For
example, an existing GitHub CLI login can provide it without placing the value in
shell history:

Run the read-only preflight first:

```bash
GITHUB_TOKEN="$(gh auth token)" kubefit publish-check \
  --proposal .kubefit/proposals/proposal-<digest> \
  --benchmark benchmarks/results/benchmark-<digest> \
  --repository-root . \
  --remote origin
```

It verifies the artifacts and clean checkout, inspects deterministic local and
remote branch state, and performs only a GitHub repository GET when the token is
present. It never creates a commit, branch, or PR. Resolve every `blockers` entry
before publication. Treat `ready` as preflight evidence rather than proof of write
permission; organization rules and token scopes are enforced only by the live write.

Then publish with an explicit acknowledgement:

```bash
GITHUB_TOKEN="$(gh auth token)" kubefit publish \
  --proposal .kubefit/proposals/proposal-<digest> \
  --benchmark benchmarks/results/benchmark-<digest> \
  --repository-root . \
  --remote origin \
  --confirm-publish
```

The token needs repository pull-request write access, while Git push uses the
repository's existing Git authentication. `--github-token-env NAME` can select a
different environment variable by name; the token value is never a CLI option.

The command creates or reuses the deterministic local commit, creates the remote
branch only if absent, and opens or reuses an exact matching Draft PR. It prints
only repository, branch, commit, PR URL/number, and reuse flags as JSON. It does not
merge, approve, mark ready, deploy, persist credentials, or delete a remote branch.
This workflow is covered by isolated local tests but has not yet been exercised
against a disposable live GitHub repository.

For the first authenticated verification, follow
[the live GitHub demonstration runbook](live-github-demo.md). It uses a separately
named private repository, makes blocked preflight exit nonzero, captures two-run
idempotency evidence outside the checkout, and archives the target by default.

## Remove the environment

This permanently deletes the local kind cluster and its Prometheus data:

```bash
./deploy/local/down.sh
```

Set `KUBEFIT_CLUSTER_NAME` to use a different cluster name. The Prometheus chart
version can be overridden temporarily with `KUBEFIT_PROMETHEUS_CHART_VERSION`, but
changes should be validated before updating the pinned repository default.

## Verify the packaged API

With Docker and the disposable cluster running, execute:

```bash
./deploy/local/verify-kubefit-chart.sh
```

The script never changes the ambient kubectl context. It builds the local image,
loads it into the explicit kind cluster, verifies the tokenless API health endpoint,
temporarily exercises namespace-scoped observation RBAC, proves denied watch/Secret/
update/cross-namespace operations, and restores tokenless defaults. It leaves the
KubeFit API release installed in `kubefit-system` but does not change the demo or
Prometheus releases.
