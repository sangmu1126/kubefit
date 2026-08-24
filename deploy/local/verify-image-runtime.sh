#!/usr/bin/env bash
set -euo pipefail

image_reference="${1:-${KUBEFIT_IMAGE_REFERENCE:-kubefit:dev}}"
container_name="kubefit-runtime-verify-$$"
container_started=false

for command_name in docker curl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

cleanup() {
  exit_status=$?
  if [[ "${container_started}" == true ]]; then
    docker rm --force "${container_name}" >/dev/null 2>&1 || true
  fi
  exit "${exit_status}"
}
trap cleanup EXIT

docker image inspect "${image_reference}" >/dev/null
runtime_user="$(docker image inspect "${image_reference}" --format '{{.Config.User}}')"
if [[ "${runtime_user}" != "10001:10001" ]]; then
  echo "KubeFit runtime image must use 10001:10001: ${runtime_user:-unset}" >&2
  exit 1
fi

docker run \
  --detach \
  --name "${container_name}" \
  --publish 127.0.0.1::8000 \
  "${image_reference}" >/dev/null
container_started=true

published_address="$(docker port "${container_name}" 8000/tcp)"
published_port="${published_address##*:}"
if ! [[ "${published_address}" == 127.0.0.1:* ]] \
  || ! [[ "${published_port}" =~ ^[0-9]+$ ]]; then
  echo "Docker returned an invalid loopback publication: ${published_address}" >&2
  exit 1
fi
base_url="http://127.0.0.1:${published_port}"

health=""
for _ in {1..30}; do
  if [[ "$(docker inspect "${container_name}" --format '{{.State.Running}}')" != "true" ]]; then
    echo "KubeFit container exited before becoming ready" >&2
    docker logs "${container_name}" >&2
    exit 1
  fi
  if health="$(curl --fail --silent --show-error "${base_url}/healthz" 2>/dev/null)"; then
    break
  fi
  sleep 1
done
if [[ "${health}" != '{"status":"ok"}' ]]; then
  echo "KubeFit health response was not ready: ${health:-empty}" >&2
  docker logs "${container_name}" >&2
  exit 1
fi

dashboard_html="$(curl --fail --silent --show-error "${base_url}/")"
if [[ "${dashboard_html}" != *"<title>KubeFit · Recommendation Review</title>"* ]]; then
  echo "packaged dashboard title was not found at the container root" >&2
  exit 1
fi

unconfigured_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "${base_url}/v1/benchmark-results/benchmark-00000000000000000000000000000000/review")"
if [[ "${unconfigured_status}" != "404" ]]; then
  echo "unconfigured benchmark storage must return 404: ${unconfigured_status}" >&2
  exit 1
fi

echo "KubeFit runtime image passed startup, health, dashboard, and disabled-storage checks."
