#!/usr/bin/env bash
set -euo pipefail

cluster_name="${KUBEFIT_CLUSTER_NAME:-kubefit}"
chart_version="${KUBEFIT_PROMETHEUS_CHART_VERSION:-88.5.0}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/../.." && pwd)"

for command_name in docker kind kubectl helm; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop and try again." >&2
  exit 1
fi

if ! kind get clusters | grep -Fxq "${cluster_name}"; then
  kind create cluster \
    --name "${cluster_name}" \
    --config "${script_dir}/kind-config.yaml"
fi

kubectl config use-context "kind-${cluster_name}" >/dev/null

helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts \
  --force-update
helm repo update
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --version "${chart_version}" \
  --values "${script_dir}/prometheus-values.yaml" \
  --wait \
  --timeout 10m

kubectl apply -f "${repository_root}/deploy/demo"
kubectl rollout status deployment/overprovisioned-api \
  --namespace kubefit-demo \
  --timeout 5m

echo
echo "KubeFit local cluster is ready."
echo "Prometheus: kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090"
