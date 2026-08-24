#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root}"

expected_version="7.6.1"
actual_version="$(pip-compile --version | awk '{print $NF}')"
if [[ "${actual_version}" != "${expected_version}" ]]; then
  echo "pip-compile ${expected_version} is required; found ${actual_version}" >&2
  exit 1
fi

common=(
  --upgrade
  --generate-hashes
  --strip-extras
  --no-header
  --no-emit-index-url
  --newline lf
  --index-url https://pypi.org/simple
)

pip-compile "${common[@]}" \
  --output-file requirements/runtime.lock \
  pyproject.toml
pip-compile "${common[@]}" \
  --extra dev \
  --output-file requirements/dev.lock \
  pyproject.toml
pip-compile "${common[@]}" \
  --all-build-deps \
  --only-build-deps \
  --output-file requirements/build.lock \
  pyproject.toml
