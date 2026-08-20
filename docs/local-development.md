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
resource usage small.

Check the resulting workloads:

```bash
kubectl get pods -n monitoring
kubectl get pods -n kubefit-demo
```

## Analyze the demo Deployment

Keep this command running in one terminal:

```bash
kubectl port-forward \
  -n monitoring \
  svc/monitoring-kube-prometheus-prometheus \
  9090:9090
```

After Prometheus has collected several samples, run in another terminal:

```bash
kubefit analyze \
  --namespace kubefit-demo \
  --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --days 1 \
  --cpu-core-hour-usd 0.04 \
  --memory-gib-hour-usd 0.005 \
  --price-source example://local-model
```

These rates are deliberately labeled example inputs. Replace them and the source
label with assumptions appropriate to the environment being evaluated. The default
billing horizon is 730 hours and can be changed with `--monthly-hours`.

A new cluster cannot provide a full one-day observation window, so KubeFit will
correctly report low observation coverage and `unknown` risk until enough samples
have accumulated. A mathematical cost projection is still shown for inspection,
but it does not make an insufficient recommendation actionable.

## Remove the environment

This permanently deletes the local kind cluster and its Prometheus data:

```bash
./deploy/local/down.sh
```

Set `KUBEFIT_CLUSTER_NAME` to use a different cluster name. The Prometheus chart
version can be overridden temporarily with `KUBEFIT_PROMETHEUS_CHART_VERSION`, but
changes should be validated before updating the pinned repository default.
