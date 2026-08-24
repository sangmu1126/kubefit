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

## Collect a controlled local demo window

Keep Prometheus and the demo Service port-forwards running in separate terminals:

```bash
kubectl --context kind-kubefit port-forward \
  -n monitoring \
  svc/monitoring-kube-prometheus-prometheus \
  9090:9090

kubectl --context kind-kubefit port-forward \
  -n kubefit-demo \
  service/overprovisioned-api \
  8080:80
```

Start the fixed one-hour traffic profile. Do not use old idle samples as controlled
demo evidence:

```bash
KUBEFIT_TARGET_URL=http://localhost:8080/ \
  k6 run benchmarks/k6/observation_profile.js
```

The profile runs 5 RPS warmup, 25 RPS steady traffic, a 100 RPS spike, then 25 RPS
recovery across exactly one hour. After it finishes, check the same one-hour window:

```bash
kubefit readiness \
  --context kind-kubefit \
  --namespace kubefit-demo \
  --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --observation-profile demo
```

`collecting` includes an estimated readiness timestamp only when replicas, Pod
metrics, and container statuses are complete. `blocked` requires intervention and
does not include a time estimate. Demo mode fixes a one-hour window, 60-second step,
90% coverage, and at least 100 samples; attempts to combine it with `--days` or
`--step-seconds` fail. Its recommendation evidence is explicitly non-production.

For a real environment, omit `--observation-profile` to retain the production
default of seven days, a five-minute step, 70% coverage, and at least 100 samples.

## Analyze the demo Deployment

When readiness becomes `eligible`, create the priced analysis artifact:

```bash
kubefit analyze \
  --context kind-kubefit \
  --namespace kubefit-demo \
  --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --observation-profile demo \
  --cpu-core-hour-usd 0.04 \
  --memory-gib-hour-usd 0.005 \
  --price-source example://local-model \
  > .kubefit/analysis.json
```

These rates are deliberately labeled example inputs. Replace them and the source
label with assumptions appropriate to the environment being evaluated. The default
billing horizon is 730 hours and can be changed with `--monthly-hours`.

A controlled demo cannot provide a complete one-hour window until its load profile
finishes, so KubeFit reports low coverage and `unknown` risk until enough samples
have accumulated. A mathematical cost projection is still shown for inspection,
but it does not make an insufficient recommendation actionable. Do not present this
short-window result as a production traffic recommendation.

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

To review the artifact created in the previous section, choose
`analysis.json 불러오기` and select `.kubefit/analysis.json`. The browser applies a
1 MiB usability guard and sends the raw JSON to `POST /v1/analysis-reviews`. The API
parses the typed artifact and recomputes resource deltas, request-cost comparison,
and patch eligibility before the existing result view renders it. This operation
does not contact Kubernetes, modify repository files, or create a proposal.

The artifact context shows Deployment UID and creation time so a screenshot does
not lose workload incarnation identity. Current `kubefit analyze` output is schema
v2 and retains aggregate `ObservedUsage` plus all recommendation policy parameters.
The API reruns recommendation, risk, cost, and eligibility and labels an exact match
`RECOMMENDATION REPLAYED`. Older schema v1 remains accepted as `INTEGRITY ONLY`.
Neither schema retains raw Prometheus time series, so the P95/P99 aggregation itself
cannot be replayed. Producer authentication and repository-byte binding are also
outside this endpoint.

The examples are editable API payloads, not live cluster collection. Uploaded
artifacts can originate from collection, but neither dashboard review label is
proof of uninterrupted live collection or percentile aggregation replay. Price
values use `example://local-model` and must not be presented as provider prices or
measured invoice savings.

### Open a fully verified benchmark link

After `kubefit benchmark` has produced an immutable result, restart the API with an
explicit results root:

```bash
KUBEFIT_BENCHMARK_RESULTS_DIRECTORY=benchmarks/results \
  uvicorn api.main:app --reload
```

Keep Vite running and open the exact result through:

```text
http://127.0.0.1:5173/?benchmark=benchmark-<digest>
```

The browser sends only the validated artifact ID. The API selects that child of the
configured root and runs the complete filesystem loader before returning the review.
The root must be a regular directory, result IDs cannot contain path components, and
symlinked result directories are rejected. Missing configuration and unknown IDs return
404 without exposing arbitrary filesystem paths.

For the packaged image, mount the results read-only and use the same query on port 8000:

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/benchmarks/results:/var/lib/kubefit/results:ro" \
  -e KUBEFIT_BENCHMARK_RESULTS_DIRECTORY=/var/lib/kubefit/results \
  kubefit:dev
```

```text
http://127.0.0.1:8000/?benchmark=benchmark-<digest>
```

`FULL ARTIFACT REPLAY` means the server rechecked the exact file set, every payload
size and SHA-256 digest, the aggregate content digest, raw k6/summary relationships,
the generated report, and the policy verdict. It still does not make a controlled
approximately 160-second run representative of production traffic. The environment
variable exposes no artifacts unless an operator deliberately provisions the directory;
KubeFit does not upload or host local evidence automatically.

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
kubectl --context kind-kubefit proxy \
  --port=8001 \
  --address=127.0.0.1 \
  --accept-hosts='^127\.0\.0\.1$'

kubectl --context kind-kubefit port-forward \
  -n monitoring service/monitoring-kube-prometheus-prometheus 9090:9090

kubefit benchmark \
  --proposal .kubefit/proposals/proposal-<digest> \
  --target-url http://127.0.0.1:8001/api/v1/namespaces/kubefit-demo/services/http:overprovisioned-api:80/proxy/ \
  --prometheus-url http://localhost:9090 \
  --context kind-kubefit \
  --confirm-disposable-cluster \
  --execution-order before-after

kubefit benchmark \
  --proposal .kubefit/proposals/proposal-<digest> \
  --target-url http://127.0.0.1:8001/api/v1/namespaces/kubefit-demo/services/http:overprovisioned-api:80/proxy/ \
  --prometheus-url http://localhost:9090 \
  --context kind-kubefit \
  --confirm-disposable-cluster \
  --execution-order after-before
```

The command locks the target Deployment, revalidates its analysis identity, restores
the before manifest on every exit path, and publishes a separate immutable result
under `benchmarks/results/`. If the repository YAML contains multiple documents,
only the selected Deployment document is applied; the complete source remains in
the proposal solely as review provenance. The fixed profile requires each phase to
meet its promised iteration minimum. Each run may contain at most one extra
scheduling-boundary iteration; missing work or two extra iterations is invalid. Use
the Kubernetes API Service proxy for the application target: `kubectl port-forward`
selects one backing Pod and disconnects when the benchmark rollout replaces it.
Prometheus can remain port-forwarded because the benchmark does not roll it out.

The two commands counterbalance chronological order and first publish independent
result artifacts. Every artifact records its actual order and emits a warning because
one sequential trial cannot separate resource effects from warm-up or time drift. The
pair command then verifies their identity, order, and policy-state agreement and, only
for PASS, publishes a self-contained immutable bundle:

```bash
kubefit benchmark-pair \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest> \
  --output-dir benchmarks/pairs
```

The JSON status is `pass` only when both fully verified artifacts reference the same
proposal, use opposite orders and identical profile/cost bases, expose identical
non-order policy check states, and both individual verdicts pass. PASS prints the
`benchmark-pair-<digest>` path and reuse state. The directory contains the canonical
assessment, report, hashes, and complete copies of both result bundles. `fail` or
`invalid` returns exit code 2 after printing the reasons and does not publish a pair.

## Publish a verified Draft PR

Publication requires a passing immutable benchmark result, a passing immutable pair
containing that result, a clean repository on an attached branch, and a credential-free
public GitHub remote. Git push authentication must already be available through an SSH
agent or Git credential helper. Supply the
GitHub API token through an environment variable, never as a command argument. For
example, an existing GitHub CLI login can provide it without placing the value in
shell history:

Run the read-only preflight first:

```bash
GITHUB_TOKEN="$(gh auth token)" kubefit publish-check \
  --proposal .kubefit/proposals/proposal-<digest> \
  --benchmark benchmarks/results/benchmark-<digest> \
  --benchmark-pair benchmarks/pairs/benchmark-pair-<digest> \
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
  --benchmark-pair benchmarks/pairs/benchmark-pair-<digest> \
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
loads it into the explicit kind cluster, verifies the tokenless API health endpoint
and packaged dashboard, temporarily exercises namespace-scoped observation RBAC,
proves denied watch/Secret/update/cross-namespace operations, and restores tokenless
defaults. It leaves the KubeFit release installed in `kubefit-system` but does not
change the demo or Prometheus releases.

## Generate a verified local image SBOM

Docker Desktop with Docker Scout and `jq` are required. After building the image,
run:

```bash
docker build --tag kubefit:dev .
./deploy/local/generate-image-sbom.sh
```

The script resolves `kubefit:dev` to its full local image ID, generates SPDX 2.3
against that ID, verifies the expected Python runtime and absence of Node/npm
packages, then atomically publishes `artifact.json` and `sbom.spdx.json` under
`.kubefit/supply-chain/image-sbom-<digest>/`. A second run validates the stored
hash, byte size, SPDX structure, and package assertions before reporting
`reused: true`.

Set `KUBEFIT_IMAGE_REFERENCE` to inspect a different local reference and
`KUBEFIT_SBOM_OUTPUT_DIR` to select another evidence directory. The output is a
package inventory only. It does not apply a CVE policy, bind the image to a Git
revision, sign evidence, or publish an OCI attachment.
