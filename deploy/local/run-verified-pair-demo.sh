#!/usr/bin/env bash
set -euo pipefail

release_version="0.3.1"
evidence_release_version="0.2.0"
pair_id="benchmark-pair-dbc41864dd0dba9537ef228ebb340f60"
archive_name="kubefit-demo-evidence-v0.2.0.tar.gz"
expected_sha256="c646b4483083f8fcedafb397d1cc2355391bc9f98b15a6b157e22b30f2793239"
download_url="https://github.com/sangmu1126/kubefit/releases/download/v${evidence_release_version}/${archive_name}"
image_reference="${KUBEFIT_DEMO_IMAGE:-ghcr.io/sangmu1126/kubefit:${release_version}}"
build_local="${KUBEFIT_DEMO_BUILD_LOCAL:-false}"
local_port="${KUBEFIT_DEMO_PORT:-8000}"
cache_root="${KUBEFIT_DEMO_CACHE_DIRECTORY:-${PWD}/.kubefit/demo/${evidence_release_version}}"
archive_path="${cache_root}/${archive_name}"
evidence_root="${cache_root}/evidence-${expected_sha256}"
pairs_directory="${evidence_root}/pairs"
pair_directory="${pairs_directory}/${pair_id}"

for command_name in docker curl tar awk mktemp; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

if ! [[ "${local_port}" =~ ^[0-9]+$ ]] \
  || ((local_port < 1 || local_port > 65535)); then
  echo "KUBEFIT_DEMO_PORT must be an integer from 1 through 65535" >&2
  exit 1
fi

if [[ "${build_local}" != "true" && "${build_local}" != "false" ]]; then
  echo "KUBEFIT_DEMO_BUILD_LOCAL must be true or false" >&2
  exit 1
fi

if [[ "${build_local}" == "true" ]]; then
  image_reference="kubefit:decision-journey"
  echo "Building the current KubeFit source for the Decision Journey..."
  docker build --tag "${image_reference}" .
fi
demo_query="showcase=decision-journey"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "sha256sum or shasum is required" >&2
    return 1
  fi
}

mkdir -p "${cache_root}"

if [[ ! -f "${archive_path}" ]] \
  || [[ "$(sha256_file "${archive_path}")" != "${expected_sha256}" ]]; then
  temporary_archive="$(mktemp "${cache_root}/.${archive_name}.XXXXXX")"
  cleanup_archive() {
    rm -f "${temporary_archive}"
  }
  trap cleanup_archive EXIT

  echo "Downloading verified KubeFit pair evidence..."
  curl --fail --location --silent --show-error \
    --output "${temporary_archive}" \
    "${download_url}"
  actual_sha256="$(sha256_file "${temporary_archive}")"
  if [[ "${actual_sha256}" != "${expected_sha256}" ]]; then
    echo "demo evidence digest mismatch: ${actual_sha256}" >&2
    exit 1
  fi
  mv "${temporary_archive}" "${archive_path}"
  trap - EXIT
fi

if [[ ! -f "${pair_directory}/pair.json" ]] \
  || [[ ! -f "${pair_directory}/assessment.json" ]]; then
  archive_entries="$(tar -tzf "${archive_path}")"
  while IFS= read -r archive_entry; do
    case "${archive_entry}" in
      /*|../*|*/../*|*/..)
        echo "demo evidence contains an unsafe archive path: ${archive_entry}" >&2
        exit 1
        ;;
    esac
  done <<< "${archive_entries}"

  temporary_evidence="$(mktemp -d "${cache_root}/.evidence.XXXXXX")"
  cleanup_evidence() {
    rm -rf "${temporary_evidence}"
  }
  trap cleanup_evidence EXIT

  tar -xzf "${archive_path}" -C "${temporary_evidence}"
  temporary_pair="${temporary_evidence}/pairs/${pair_id}"
  for required_file in pair.json assessment.json report.md; do
    if [[ ! -f "${temporary_pair}/${required_file}" ]]; then
      echo "demo evidence is missing ${required_file}" >&2
      exit 1
    fi
  done
  if [[ -e "${evidence_root}" ]]; then
    echo "cached demo evidence is incomplete: ${evidence_root}" >&2
    exit 1
  fi
  mv "${temporary_evidence}" "${evidence_root}"
  trap - EXIT
fi

demo_url="http://127.0.0.1:${local_port}/?${demo_query}"
echo "Starting KubeFit verified decision dashboard:"
echo "  ${demo_url}"
echo "Press Ctrl+C to stop the local container."

exec docker run --rm \
  --name "kubefit-verified-pair-demo-$$" \
  --label io.kubefit.demo=verified-pair \
  --publish "127.0.0.1:${local_port}:8000" \
  --volume "${pairs_directory}:/var/lib/kubefit/pairs:ro" \
  --env KUBEFIT_BENCHMARK_PAIRS_DIRECTORY=/var/lib/kubefit/pairs \
  "${image_reference}"
