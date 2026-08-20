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

## Analyze the demo Deployment

Keep this command running in one terminal:

```bash
kubectl --context kind-kubefit port-forward \
  -n monitoring \
  svc/monitoring-kube-prometheus-prometheus \
  9090:9090
```

After Prometheus has collected several samples, run in another terminal:

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

## Remove the environment

This permanently deletes the local kind cluster and its Prometheus data:

```bash
./deploy/local/down.sh
```

Set `KUBEFIT_CLUSTER_NAME` to use a different cluster name. The Prometheus chart
version can be overridden temporarily with `KUBEFIT_PROMETHEUS_CHART_VERSION`, but
changes should be validated before updating the pinned repository default.
