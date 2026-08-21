#!/usr/bin/env bash
set -euo pipefail

image_reference="${KUBEFIT_IMAGE_REFERENCE:-kubefit:dev}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/../.." && pwd)"
output_root="${KUBEFIT_SBOM_OUTPUT_DIR:-${repository_root}/.kubefit/supply-chain}"
staging_directory=""

for command_name in docker jq; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "required command is not installed: ${command_name}" >&2
    exit 1
  fi
done
if ! command -v sha256sum >/dev/null 2>&1 \
  && ! command -v shasum >/dev/null 2>&1; then
  echo "required SHA-256 command is not installed: sha256sum or shasum" >&2
  exit 1
fi
if ! docker scout version >/dev/null 2>&1; then
  echo "Docker Scout is not available" >&2
  exit 1
fi

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d ' ' -f 1
  else
    shasum -a 256 "$1" | cut -d ' ' -f 1
  fi
}

cleanup() {
  exit_status=$?
  if [[ -n "${staging_directory}" && -d "${staging_directory}" ]]; then
    rm -rf -- "${staging_directory}"
  fi
  exit "${exit_status}"
}
trap cleanup EXIT

image_id="$(docker image inspect --format '{{.Id}}' "${image_reference}")"
platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${image_reference}")"
runtime_user="$(docker image inspect --format '{{.Config.User}}' "${image_reference}")"
image_created_at="$(docker image inspect --format '{{.Created}}' "${image_reference}")"

if ! [[ "${image_id}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Docker returned an invalid image ID: ${image_id}" >&2
  exit 1
fi
if [[ "${platform}" != linux/* ]]; then
  echo "KubeFit runtime image must target Linux: ${platform}" >&2
  exit 1
fi
if [[ "${runtime_user}" != "10001:10001" ]]; then
  echo "KubeFit runtime image must use 10001:10001: ${runtime_user:-unset}" >&2
  exit 1
fi

image_digest="${image_id#sha256:}"
artifact_id="image-sbom-${image_digest:0:32}"
mkdir -p "${output_root}"
output_root="$(cd "${output_root}" && pwd)"
artifact_directory="${output_root}/${artifact_id}"

verify_artifact() {
  local directory=$1
  local manifest="${directory}/artifact.json"
  local sbom="${directory}/sbom.spdx.json"
  if [[ ! -f "${manifest}" || ! -f "${sbom}" ]]; then
    echo "existing SBOM artifact is incomplete: ${directory}" >&2
    return 1
  fi
  if [[ "$(jq -er '.image.id' "${manifest}")" != "${image_id}" ]]; then
    echo "existing SBOM artifact image ID changed" >&2
    return 1
  fi
  expected_sha256="$(jq -er '.sbom.sha256' "${manifest}")"
  if [[ "$(sha256_file "${sbom}")" != "${expected_sha256}" ]]; then
    echo "existing SBOM digest changed" >&2
    return 1
  fi
  expected_bytes="$(jq -er '.sbom.byte_size' "${manifest}")"
  if [[ "$(wc -c < "${sbom}" | tr -d ' ')" != "${expected_bytes}" ]]; then
    echo "existing SBOM byte size changed" >&2
    return 1
  fi
  jq -e '
    .spdxVersion == "SPDX-2.3"
    and (.packages | type == "array" and length > 0)
    and any(.packages[]; .name == "kubefit")
    and any(.packages[]; .name == "fastapi")
    and any(.packages[]; .name == "uvicorn")
    and (any(.packages[]; .name == "node" or .name == "npm") | not)
  ' "${sbom}" >/dev/null
}

if [[ -e "${artifact_directory}" ]]; then
  if [[ ! -d "${artifact_directory}" ]]; then
    echo "SBOM artifact path is not a directory: ${artifact_directory}" >&2
    exit 1
  fi
  verify_artifact "${artifact_directory}"
  jq -n \
    --arg artifact_id "${artifact_id}" \
    --arg path "${artifact_directory}" \
    --arg image_id "${image_id}" \
    --argjson package_count "$(jq '.packages | length' "${artifact_directory}/sbom.spdx.json")" \
    '{artifact_id: $artifact_id, path: $path, image_id: $image_id, package_count: $package_count, reused: true}'
  exit 0
fi

staging_directory="$(mktemp -d "${output_root}/.image-sbom.XXXXXX")"
sbom_path="${staging_directory}/sbom.spdx.json"
docker scout sbom \
  --format spdx \
  --output "${sbom_path}" \
  "local://${image_id}"

jq -e '
  .spdxVersion == "SPDX-2.3"
  and .dataLicense == "CC0-1.0"
  and (.packages | type == "array" and length > 0)
  and any(.packages[]; .name == "kubefit")
  and any(.packages[]; .name == "fastapi")
  and any(.packages[]; .name == "uvicorn")
  and (any(.packages[]; .name == "node" or .name == "npm") | not)
' "${sbom_path}" >/dev/null

sbom_sha256="$(sha256_file "${sbom_path}")"
sbom_byte_size="$(wc -c < "${sbom_path}" | tr -d ' ')"
package_count="$(jq '.packages | length' "${sbom_path}")"
document_namespace="$(jq -er '.documentNamespace' "${sbom_path}")"
sbom_created_at="$(jq -er '.creationInfo.created' "${sbom_path}")"
generator="$(jq -er '.creationInfo.creators[] | select(startswith("Tool: "))' "${sbom_path}" | head -n 1)"

jq -n \
  --argjson schema_version 1 \
  --arg artifact_id "${artifact_id}" \
  --arg requested_reference "${image_reference}" \
  --arg image_id "${image_id}" \
  --arg platform "${platform}" \
  --arg runtime_user "${runtime_user}" \
  --arg image_created_at "${image_created_at}" \
  --arg format "SPDX-2.3" \
  --arg path "sbom.spdx.json" \
  --arg sha256 "${sbom_sha256}" \
  --argjson byte_size "${sbom_byte_size}" \
  --argjson package_count "${package_count}" \
  --arg document_namespace "${document_namespace}" \
  --arg generated_at "${sbom_created_at}" \
  --arg generator "${generator}" \
  '{
    schema_version: $schema_version,
    artifact_id: $artifact_id,
    image: {
      requested_reference: $requested_reference,
      id: $image_id,
      platform: $platform,
      runtime_user: $runtime_user,
      created_at: $image_created_at
    },
    sbom: {
      format: $format,
      path: $path,
      sha256: $sha256,
      byte_size: $byte_size,
      package_count: $package_count,
      document_namespace: $document_namespace,
      generated_at: $generated_at,
      generator: $generator
    },
    assertions: {
      required_packages: ["kubefit", "fastapi", "uvicorn"],
      forbidden_runtime_packages: ["node", "npm"]
    }
  }' > "${staging_directory}/artifact.json"

verify_artifact "${staging_directory}"
mv "${staging_directory}" "${artifact_directory}"
staging_directory=""

jq -n \
  --arg artifact_id "${artifact_id}" \
  --arg path "${artifact_directory}" \
  --arg image_id "${image_id}" \
  --argjson package_count "${package_count}" \
  '{artifact_id: $artifact_id, path: $path, image_id: $image_id, package_count: $package_count, reused: false}'
