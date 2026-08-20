#!/usr/bin/env bash
set -euo pipefail

cluster_name="${KUBEFIT_CLUSTER_NAME:-kubefit}"
cluster_context="kind-${cluster_name}"
release_name="${KUBEFIT_HELM_RELEASE:-kubefit}"
release_namespace="${KUBEFIT_HELM_NAMESPACE:-kubefit-system}"
target_namespace="${KUBEFIT_RBAC_TARGET_NAMESPACE:-kubefit-demo}"
image_repository="${KUBEFIT_IMAGE_REPOSITORY:-kubefit}"
image_tag="${KUBEFIT_IMAGE_TAG:-dev}"
local_port="${KUBEFIT_HEALTH_PORT:-18080}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/../.." && pwd)"
chart_path="${repository_root}/deploy/helm/kubefit"
service_account="${release_name}"
port_forward_pid=""
port_forward_log="$(mktemp "${TMPDIR:-/tmp}/kubefit-port-forward.XXXXXX")"
restore_needed=false

for command_name in docker kind kubectl helm curl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

if [[ "${cluster_context}" != kind-* ]]; then
  echo "chart verification requires an explicit kind-* context" >&2
  exit 1
fi
if ! [[ "${local_port}" =~ ^[0-9]+$ ]] || ((local_port < 1 || local_port > 65535)); then
  echo "KUBEFIT_HEALTH_PORT must be an integer from 1 to 65535" >&2
  exit 1
fi

default_release() {
  helm upgrade --install "${release_name}" "${chart_path}" \
    --kube-context "${cluster_context}" \
    --namespace "${release_namespace}" \
    --create-namespace \
    --reset-values \
    --set "fullnameOverride=${release_name}" \
    --set "image.repository=${image_repository}" \
    --set "image.tag=${image_tag}" \
    --set image.pullPolicy=Never \
    --set serviceAccount.automountToken=false \
    --wait \
    --timeout 3m
}

cleanup() {
  exit_status=$?
  if [[ -n "${port_forward_pid}" ]]; then
    kill "${port_forward_pid}" >/dev/null 2>&1 || true
    wait "${port_forward_pid}" >/dev/null 2>&1 || true
  fi
  if [[ "${restore_needed}" == true ]]; then
    echo "Restoring tokenless KubeFit release after interrupted verification." >&2
    default_release >/dev/null || echo "WARNING: failed to restore default release" >&2
  fi
  rm -f "${port_forward_log}"
  exit "${exit_status}"
}
trap cleanup EXIT

assert_can_i() {
  expected=$1
  verb=$2
  resource=$3
  namespace=$4
  command_status=0
  actual="$(kubectl --context "${cluster_context}" auth can-i \
    "${verb}" "${resource}" \
    --namespace "${namespace}" \
    --as "system:serviceaccount:${release_namespace}:${service_account}")" \
    || command_status=$?
  if ((command_status > 1)); then
    echo "kubectl auth can-i failed with exit ${command_status}: ${verb} ${resource} in ${namespace}" >&2
    exit 1
  fi
  if [[ "${actual}" != "${expected}" ]]; then
    echo "RBAC mismatch: expected ${expected}, got ${actual}: ${verb} ${resource} in ${namespace}" >&2
    exit 1
  fi
  printf 'RBAC %-3s  %-6s %-45s namespace=%s\n' "${actual}" "${verb}" "${resource}" "${namespace}"
}

docker info >/dev/null
if ! kind get clusters | grep -Fxq "${cluster_name}"; then
  echo "required disposable kind cluster does not exist: ${cluster_name}" >&2
  exit 1
fi

docker build --tag "${image_repository}:${image_tag}" "${repository_root}"
kind load docker-image --name "${cluster_name}" "${image_repository}:${image_tag}"

default_release
kubectl --context "${cluster_context}" \
  --namespace "${release_namespace}" \
  rollout status "deployment/${release_name}" --timeout 3m

kubectl --context "${cluster_context}" \
  --namespace "${release_namespace}" \
  port-forward "service/${release_name}" "${local_port}:80" \
  >"${port_forward_log}" 2>&1 &
port_forward_pid=$!
for _ in {1..30}; do
  if curl --fail --silent --show-error "http://127.0.0.1:${local_port}/healthz" >/dev/null; then
    break
  fi
  if ! kill -0 "${port_forward_pid}" >/dev/null 2>&1; then
    cat "${port_forward_log}" >&2
    exit 1
  fi
  sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${local_port}/healthz"
echo

assert_can_i no get "deployment/overprovisioned-api" "${target_namespace}"

restore_needed=true
helm upgrade --install "${release_name}" "${chart_path}" \
  --kube-context "${cluster_context}" \
  --namespace "${release_namespace}" \
  --create-namespace \
  --reset-values \
  --set "fullnameOverride=${release_name}" \
  --set "image.repository=${image_repository}" \
  --set "image.tag=${image_tag}" \
  --set image.pullPolicy=Never \
  --set serviceAccount.automountToken=true \
  --set "rbac.targetNamespaces[0]=${target_namespace}" \
  --wait \
  --timeout 3m

assert_can_i yes get "deployment/overprovisioned-api" "${target_namespace}"
assert_can_i yes list replicasets "${target_namespace}"
assert_can_i yes list pods "${target_namespace}"
assert_can_i no watch pods "${target_namespace}"
assert_can_i no get secrets "${target_namespace}"
assert_can_i no update "deployment/overprovisioned-api" "${target_namespace}"
assert_can_i no list pods monitoring

default_release
restore_needed=false
assert_can_i no get "deployment/overprovisioned-api" "${target_namespace}"

echo "KubeFit chart verification passed; tokenless defaults restored."
