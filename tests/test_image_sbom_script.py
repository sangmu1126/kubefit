import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/local/generate-image-sbom.sh"
IMAGE_ID = "sha256:" + "a" * 64


def spdx(packages: list[str] | None = None) -> dict[str, object]:
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "name": "kubefit",
        "documentNamespace": "https://example.test/kubefit-sbom",
        "creationInfo": {
            "created": "2026-08-21T00:00:00Z",
            "creators": ["Tool: docker-scout-test"],
        },
        "packages": [
            {"SPDXID": f"SPDXRef-{name}", "name": name, "versionInfo": "1"}
            for name in (packages or ["kubefit", "fastapi", "uvicorn"])
        ],
        "relationships": [],
    }


def fake_docker(directory: Path) -> Path:
    executable = directory / "docker"
    executable.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "scout version" ]]; then
  echo "v-test"
  exit 0
fi
if [[ "$1 $2" == "image inspect" ]]; then
  format="$4"
  case "${format}" in
    '{{.Id}}') echo "${FAKE_IMAGE_ID}" ;;
    '{{.Os}}/{{.Architecture}}') echo "linux/arm64" ;;
    '{{.Config.User}}') echo "10001:10001" ;;
    '{{.Created}}') echo "2026-08-21T00:00:00Z" ;;
    *) echo "unexpected inspect format: ${format}" >&2; exit 2 ;;
  esac
  exit 0
fi
if [[ "$1 $2" == "scout sbom" ]]; then
  while (($#)); do
    if [[ "$1" == "--output" ]]; then
      cp "${FAKE_SBOM_SOURCE}" "$2"
      exit 0
    fi
    shift
  done
fi
echo "unexpected docker arguments: $*" >&2
exit 2
"""
    )
    executable.chmod(0o755)
    return executable


def run_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    fake_docker(tools)
    source = tmp_path / "source.spdx.json"
    source.write_text(json.dumps(document))
    output = tmp_path / "artifacts"
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_IMAGE_ID", IMAGE_ID)
    monkeypatch.setenv("FAKE_SBOM_SOURCE", str(source))
    monkeypatch.setenv("KUBEFIT_SBOM_OUTPUT_DIR", str(output))
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True)


def test_sbom_script_publishes_and_reuses_verified_image_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run_script(tmp_path, monkeypatch, spdx())
    assert first.returncode == 0, first.stderr
    first_result = json.loads(first.stdout)
    assert first_result["reused"] is False
    artifact = Path(first_result["path"])
    manifest = json.loads((artifact / "artifact.json").read_text())
    assert manifest["image"]["id"] == IMAGE_ID
    assert manifest["image"]["runtime_user"] == "10001:10001"
    assert manifest["sbom"]["package_count"] == 3

    second = run_script(tmp_path, monkeypatch, spdx())
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["reused"] is True

    (artifact / "sbom.spdx.json").write_text("{}")
    tampered = run_script(tmp_path, monkeypatch, spdx())
    assert tampered.returncode != 0
    assert "existing SBOM digest changed" in tampered.stderr


def test_sbom_script_rejects_missing_runtime_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_script(tmp_path, monkeypatch, spdx(["fastapi", "uvicorn"]))

    assert result.returncode != 0
    assert not list((tmp_path / "artifacts").glob("image-sbom-*"))
