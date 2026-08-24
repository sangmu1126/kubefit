import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "local" / "verify-image-runtime.sh"


def test_runtime_smoke_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_runtime_smoke_script_checks_packaged_service_boundaries() -> None:
    source = SCRIPT.read_text()

    assert "--publish 127.0.0.1::8000" in source
    assert '"${base_url}/healthz"' in source
    assert "<title>KubeFit · Recommendation Review</title>" in source
    assert "unconfigured benchmark storage must return 404" in source
    assert "10001:10001" in source
    assert "{{.State.Running}}" in source
    assert "docker logs" in source
    assert 'docker rm --force "${container_name}"' in source
