#!/usr/bin/env bash
set -euo pipefail

cluster_name="${KUBEFIT_CLUSTER_NAME:-kubefit}"

if ! command -v kind >/dev/null 2>&1; then
  echo "required command is not installed: kind" >&2
  exit 1
fi

kind delete cluster --name "${cluster_name}"
